"""One live model job, durable artifacts, and idempotent MySQL run registration."""

import asyncio
import json
import re
from contextlib import asynccontextmanager
from pathlib import Path
from threading import Lock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from mewhelp.db.models import EvalRun
from mewhelp.knowledge.refusals import utc_now
from mewhelp.knowledge.retrieval import RetrievalRuntime

from .contracts import RequestTraceContext
from .evaluation import atomic_json, evaluate_current_path, settled_thread, validate_run
from .locks import named_lock
from .observability import get_observation_runtime
from .snapshots import same_json_value


class EvaluationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(min_length=1, max_length=64)
    triggered_by: Literal["手动", "定时"] = "手动"
    resume: bool = False

    @field_validator("run_id")
    @classmethod
    def safe_id(cls, value):
        if not re.fullmatch("[a-z][a-z0-9_]{0,63}", value):
            raise ValueError("run_id格式无效")
        return value


class EvaluationJob(BaseModel):
    model_config = ConfigDict(frozen=True)
    run_id: str
    status: str
    processed: int = Field(default=0, ge=0, le=40)
    dataset_size: int = 40
    artifact_dir: str
    error: str | None = None
    eval_run_id: str | None = None


def persist_eval_run(factory, *, request, summary, engine):
    if (
        summary.get("dataset_size") != 40
        or summary.get("processed") != 40
        or not summary.get("complete")
    ):
        raise ValueError("只有完整40条终态评估才能登记")
    metrics = summary["metrics"]
    if metrics.get("N") != 40 or summary.get("complete") is not True:
        raise ValueError("必须有完整40条实际处理记录")
    meta = metrics.get("_meta", {})
    if (
        meta.get("run_id") != request.run_id
        or not meta.get("identity_hash")
        or not meta.get("result_hash")
    ):
        raise ValueError("评估来源身份不完整")
    with named_lock(engine, scope="eval-register", key=request.run_id, wait_seconds=5) as acquired:
        if not acquired:
            raise RuntimeError("评估登记忙，请重试")
        with factory.begin() as db:
            row = db.scalar(
                select(EvalRun)
                .where(EvalRun.metrics[("_meta", "run_id")].as_string() == request.run_id)
                .with_for_update()
            )
            if row:
                if not same_json_value(row.metrics, metrics) or row.triggered_by != request.triggered_by:
                    raise ValueError("同一run_id对应不同评估结果或触发方式")
                return str(row.id)
            row = EvalRun(
                triggered_by=request.triggered_by,
                dataset_size=40,
                metrics=metrics,
                created_at=utc_now(),
            )
            db.add(row)
            db.flush()
            return str(row.id)


@asynccontextmanager
async def evaluation_lock(engine):
    lock = named_lock(engine, scope="evaluation")
    entry = asyncio.create_task(asyncio.to_thread(lock.__enter__))
    try:
        acquired = await asyncio.shield(entry)
    except asyncio.CancelledError:
        await entry
        await asyncio.to_thread(lock.__exit__, None, None, None)
        raise
    try:
        if not acquired:
            raise RuntimeError("另一评估任务正忙")
        yield
    finally:
        await asyncio.to_thread(lock.__exit__, None, None, None)


class OriginalEncodingCache:
    def __init__(self, embed):
        self.embed = embed
        self.cache = {}
        self.lock = Lock()

    def __call__(self, texts):
        # Raw questions still perform their own encoding; cache only canonical chunk originals.
        if not all(t.startswith("分类：") and "\n问题：" in t and "\n答案：" in t for t in texts):
            return self.embed(texts)
        with self.lock:
            missing = list(dict.fromkeys(t for t in texts if t not in self.cache))
            if missing:
                vectors = self.embed(missing)
                if len(vectors) != len(missing) or any(len(v) != 1024 for v in vectors):
                    raise ValueError("原文编码缓存收到无效向量")
                self.cache.update(zip(missing, vectors, strict=True))
            return [list(self.cache[t]) for t in texts]


class EvalJobManager:
    def __init__(
        self,
        factory,
        *,
        dataset,
        artifact_dir,
        profile,
        workflow=None,
        runner=evaluate_current_path,
        observations=None,
    ):
        self.factory = factory
        self.engine = factory.kw["bind"]
        self.dataset = Path(dataset)
        self.root = Path(artifact_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.profile = profile
        self.workflow = workflow
        self.runner = runner
        self.observations = observations or get_observation_runtime()
        self.jobs = {}
        self.task = None
        self.mutex = asyncio.Lock()
        self.shared = None

    def get(self, run_id):
        validate_run(run_id, self.root / run_id)
        if run_id in self.jobs:
            return self.jobs[run_id]
        workdir = self.root / run_id
        path = workdir / "manifest.json"
        receipt = self.root / (run_id + ".job.json")
        if not path.exists():
            if not receipt.exists():
                raise LookupError("评估任务不存在")
            job = EvaluationJob.model_validate(
                json.loads(receipt.read_text(encoding="utf-8"))["job"]
            )
            return job.model_copy(
                update={"status": "interrupted" if job.status == "running" else job.status}
            )
        manifest = json.loads(path.read_text(encoding="utf-8"))
        status = manifest["status"]
        if status in {"running", "preparing"}:
            status = "interrupted"
        with self.factory() as db:
            row = db.scalar(
                select(EvalRun).where(EvalRun.metrics[("_meta", "run_id")].as_string() == run_id)
            )
            row_id = str(row.id) if row else None
        return EvaluationJob(
            run_id=run_id,
            status=status,
            processed=manifest.get("processed", 0),
            artifact_dir=str(workdir.resolve()),
            error=manifest.get("error"),
            eval_run_id=row_id,
        )

    async def submit(self, request):
        async with self.mutex:
            receipt = self.root / (request.run_id + ".job.json")
            if receipt.exists():
                old = json.loads(receipt.read_text(encoding="utf-8"))
                if old["request"]["triggered_by"] != request.triggered_by:
                    raise ValueError("同一轮的初始触发方式不能改变")
            existing = self.jobs.get(request.run_id)
            if existing and existing.status == "running":
                return existing
            if self.task and not self.task.done():
                raise RuntimeError("评估任务正忙")
            if existing and existing.eval_run_id:
                return existing
            workdir = self.root / request.run_id
            validate_run(request.run_id, workdir)
            if (workdir.exists() or receipt.exists()) and not request.resume:
                old = await asyncio.to_thread(self.get, request.run_id)
                if old.eval_run_id:
                    return old
                raise ValueError("旧轮未完成登记，必须显式resume或使用新run_id")
            lock = evaluation_lock(self.engine)
            await lock.__aenter__()
            job = EvaluationJob(
                run_id=request.run_id, status="running", artifact_dir=str(workdir.resolve())
            )
            self.jobs[request.run_id] = job
            try:
                atomic_json(receipt, {"request": request.model_dump(), "job": job.model_dump()})
                self.task = asyncio.create_task(
                    self.run(request, lock), name="ch09-evaluation-" + request.run_id
                )
            except BaseException:
                self.jobs.pop(request.run_id, None)
                await lock.__aexit__(None, None, None)
                raise
            return job

    async def run(self, request, lock):
        run_id = request.run_id

        def progress(count):
            self.jobs[run_id] = self.jobs[run_id].model_copy(update={"processed": count})

        try:
            if self.workflow and self.shared is None:
                online = await settled_thread(self.workflow.rag_factory)
                r = online.retrieval
                self.shared = RetrievalRuntime(
                    r.session_factory, OriginalEncodingCache(r.embed), r.index, r.rerank
                )
            ctx = RequestTraceContext(
                entry_point="evaluation", trace_kind="evaluation", turn_id=run_id
            )
            with self.observations.request(ctx, input=request.model_dump()) as root:
                result = await self.runner(
                    dataset=self.dataset,
                    workdir=self.root / run_id,
                    run_id=run_id,
                    profile=self.profile,
                    resume=request.resume and (self.root / run_id / "manifest.json").exists(),
                    retrieval_runtime=self.shared,
                    workflow_context=self.workflow,
                    on_progress=progress,
                    triggered_by=request.triggered_by,
                )
                row_id = await asyncio.to_thread(
                    persist_eval_run,
                    self.factory,
                    request=request,
                    summary=result,
                    engine=self.engine,
                )
                root.finish(
                    status="completed" if result["status"] == "completed" else "error",
                    output=result,
                )
            self.jobs[run_id] = self.jobs[run_id].model_copy(
                update={
                    "status": result["status"],
                    "processed": result["processed"],
                    "eval_run_id": row_id,
                }
            )
        except asyncio.CancelledError:
            self.jobs[run_id] = self.jobs[run_id].model_copy(
                update={"status": "interrupted", "error": "服务关闭；本轮已完成项保存在独立产物"}
            )
            raise
        except Exception as exc:  # noqa: BLE001 — job status preserves any real failure
            self.jobs[run_id] = self.jobs[run_id].model_copy(
                update={"status": "error", "error": f"{type(exc).__name__}: {exc}"[:512]}
            )
        finally:
            try:
                atomic_json(
                    self.root / (run_id + ".job.json"),
                    {"request": request.model_dump(), "job": self.jobs[run_id].model_dump()},
                )
            finally:
                await lock.__aexit__(None, None, None)

    async def aclose(self):
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
