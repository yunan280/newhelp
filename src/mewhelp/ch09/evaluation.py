"""Frozen test-only evaluation of the current raw retrieval/gate/answer contract."""

import asyncio
import hashlib
import importlib.metadata
import inspect
import json
import logging
import re
import time
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from mewhelp.ch05.config import get_ch05_model
from mewhelp.ch05.evidence import EvidenceEnvelope, evaluate_gate, retrieve_current_evidence
from mewhelp.ch05.limits import AgentLimits
from mewhelp.ch05.state import WorkflowContext
from mewhelp.config import get_settings
from mewhelp.knowledge.answering import REFUSAL_MESSAGE, source_dtos
from mewhelp.knowledge.evaluation.dataset import load_dataset
from mewhelp.knowledge.evaluation.judge import JUDGE_SYSTEM, judge_answer
from mewhelp.knowledge.evaluation.metrics import recall_at_k, reciprocal_rank_at_k
from mewhelp.knowledge.evaluation.runner import _factory, _new_index, _snapshots, summarize
from mewhelp.knowledge.retrieval import RetrievalResult, RetrievalRuntime
from mewhelp.knowledge.store import _content_hash, put_chunk, source_id
from mewhelp.knowledge.sync import reindex_all
from mewhelp.knowledge.vectors import MilvusSettings

from .confidence import current_profile_identity, score_evidence
from .generation import KNOWLEDGE_ANSWER_SYSTEM, generate_knowledge_answer
from .observability import observed_sync_call
from .snapshots import snapshot_result


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, default=str).encode()
    ).hexdigest()


async def settled_thread(function, *args, **kwargs):
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        except Exception:
            logging.getLogger(__name__).exception("评估取消时同步任务也发生错误")
        raise


def atomic_text(path, text):
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def atomic_json(path, value):
    atomic_text(
        path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False, default=str) + "\n"
    )


def atomic_rows(path, rows):
    for row in rows:
        row["_record_hash"] = digest({k: v for k, v in row.items() if k != "_record_hash"})
    atomic_text(
        path,
        "".join(
            json.dumps(row, ensure_ascii=False, allow_nan=False, default=str) + "\n" for row in rows
        ),
    )


def validate_run(run_id, workdir, *, online_collection=None):
    collection = "ch04_eval_" + run_id
    if (
        not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", run_id)
        or collection == (online_collection or MilvusSettings().milvus_collection)
        or not Path(workdir).name
    ):
        raise ValueError("评估必须使用隔离run_id、SQLite与ch04_eval_<run_id>集合")
    if (
        Path(workdir).is_symlink()
        or (Path(workdir) / "corpus").is_symlink()
        or (Path(workdir) / "corpus/source.sqlite3").is_symlink()
    ):
        raise ValueError("评估资源不得通过符号链接指向其他数据")
    return collection


def resume_rows(workdir, expected, case_ids):
    old = json.loads((workdir / "manifest.json").read_text(encoding="utf-8"))
    if (
        old.get("identity_hash") != expected["identity_hash"]
        or old.get("run_id") != expected["run_id"]
    ):
        raise ValueError("评估模型、Prompt、数据或配置漂移，不能复用旧轮")
    path = workdir / "cases.jsonl"
    rows = (
        [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines()]
        if path.exists()
        else []
    )
    ids = [r.get("id") for r in rows]
    if (
        len(set(ids)) != len(ids)
        or not set(ids) <= case_ids
        or any(r.get("terminal") is not True for r in rows)
    ):
        raise ValueError("旧评估终态记录与本轮测试集不匹配")
    if any(
        r.get("_record_hash") != digest({k: v for k, v in r.items() if k != "_record_hash"})
        for r in rows
    ):
        raise ValueError("旧评估记录完整性校验失败")
    return rows


def summarize_rows(rows):
    return summarize(rows)


def make_judge(workflow):
    from langchain_core.utils.function_calling import convert_to_openai_tool

    from mewhelp.ch07.budget import check_window
    from mewhelp.knowledge.evaluation.judge import ClaimBatch

    from .observability import with_callbacks

    class Judge:
        usage = None
        raw = None

        async def __call__(self, question, answer, sources):
            owner = self
            output = min(4096, workflow.limits.final_max_tokens)
            bound = with_callbacks(
                workflow.model_factory(output, streaming=False).with_structured_output(
                    ClaimBatch, method="function_calling", include_raw=True
                )
            )

            class Capture:
                async def ainvoke(self, messages):
                    check_window(
                        messages,
                        [convert_to_openai_tool(ClaimBatch)],
                        settings=workflow.settings,
                        profile=workflow.profile,
                        output_tokens=output,
                        remaining_tool_calls=0,
                    )
                    envelope = await asyncio.wait_for(
                        bound.ainvoke(messages), timeout=workflow.limits.request_seconds
                    )
                    raw = envelope.get("raw") if isinstance(envelope, dict) else None
                    owner.usage = getattr(raw, "usage_metadata", None) or getattr(
                        raw, "response_metadata", {}
                    ).get("token_usage")
                    owner.raw = raw.model_dump(mode="json") if raw is not None else None
                    return envelope

            return await judge_answer(question, answer, sources, model=Capture())

    return Judge()


def context_ceiling():
    value = get_settings().rag_context_budget
    if value is None:
        raise ValueError("RAG_CONTEXT_BUDGET必须显式配置")
    return value


async def evaluate_case(
    case, *, retrieval, workflow, profile, generator=generate_knowledge_answer, judge=None
):
    started = time.perf_counter()
    row = {
        "id": case.id,
        "question": case.question,
        "query_type": case.query_type,
        "difficulty": case.difficulty,
        "should_refuse": case.should_refuse,
        "ground_truth": sorted(str(i) for i in case.relevant_chunk_ids),
        "filters": case.filters.model_dump(),
        "refused": None,
        "error": None,
        "judge_error": None,
        "faithfulness": None,
        "candidate_recall50": None,
        "candidate_mrr50": None,
        "final_recall5": None,
        "final_recall10": None,
        "final_mrr10": None,
        "generation_usage": None,
        "judge_usage": None,
        "claims": [],
        "terminal": True,
    }
    stage = "retrieval"
    try:
        evidence = await settled_thread(
            observed_sync_call,
            "evaluation.raw_hybrid_rerank",
            {"question": case.question, "filters": case.filters.model_dump()},
            retrieve_current_evidence,
            case.question,
            rag=SimpleNamespace(retrieval=retrieval),
            filters=case.filters,
        )
        row["candidates"] = [asdict(c) for c in evidence.candidates]
        row["final"] = [asdict(r) for r in evidence.final]
        candidate_ids = [c.id for c in evidence.candidates]
        final_ids = [r.chunk.id for r in evidence.final]
        gt = case.relevant_chunk_ids
        row.update(
            candidate_recall50=recall_at_k(candidate_ids, gt, 50),
            candidate_mrr50=reciprocal_rank_at_k(candidate_ids, gt, 50),
            final_recall5=recall_at_k(final_ids, gt, 5),
            final_recall10=recall_at_k(final_ids, gt, 10),
            final_mrr10=reciprocal_rank_at_k(final_ids, gt, 10),
        )
        confidence = asdict(score_evidence([r.score for r in evidence.final], profile=profile))
        top = RetrievalResult(
            evidence.candidates,
            evidence.final[: profile.top_k],
            evidence.unsupported_context_reason,
        )
        sources = source_dtos(top)
        snapshot = snapshot_result(
            top,
            query=case.question,
            filters=case.filters,
            top_k=profile.top_k,
            confidence=confidence,
        )
        envelope = EvidenceEnvelope(
            sources=sources,
            scores=[r.score for r in top.final],
            threshold=0,
            context_budget=context_ceiling(),
            unsupported_reason=evidence.unsupported_context_reason,
            retrieved_chunks=snapshot.model_dump(mode="json"),
        )
        gate = evaluate_gate(
            envelope,
            prompt_bytes=len(
                json.dumps([s.model_dump() for s in sources], ensure_ascii=False).encode()
            ),
        )
        if gate.passed:
            gate = gate.model_copy(
                update={
                    "passed": confidence["passed"],
                    "reason_code": confidence["reason_code"],
                    "reason": confidence["reason"],
                }
            )
        row["gate"] = {**gate.model_dump(), "evidence_confidence": confidence}
        if not gate.passed:
            row.update(refused=True, answer=REFUSAL_MESSAGE)
            return row
        stage = "generation"
        emitted = {}

        def emit(event):
            if event["event"] == "sources":
                emitted["sources"] = event["data"]["sources"]

        state = {
            "question": case.question,
            "resolved_question": case.question,
            "route": "knowledge",
            "intent": "knowledge",
            "entry_point": "cli",
            "filters": case.filters.model_dump(),
            "started_at": time.time(),
            "usage": {},
            "calls": {},
            "evidence": envelope.model_dump(),
            "gate": row["gate"],
        }
        result = await generator(state, workflow, emit, record_pool=False)
        if "refused" not in result:
            raise RuntimeError("生成未产生已校验的回答或拒答终态")
        row.update(
            answer=result["answer"],
            refused=result["refused"],
            generation_usage=result.get("knowledge_raw_usage"),
            assessment=result.get("knowledge_assessment"),
            generation_stop_reason=result.get("stop_reason"),
        )
        if row["refused"]:
            return row
        stage = "judge"
        from mewhelp.knowledge.answering import SourceDTO

        judged_sources = [
            SourceDTO.model_validate(s) for s in emitted.get("sources", [])
        ] or sources
        evaluator = judge or make_judge(workflow)
        judgement = await evaluator(case.question, row["answer"], judged_sources)
        row.update(
            claims=[
                asdict(c) if not hasattr(c, "model_dump") else c.model_dump()
                for c in judgement.claims
            ],
            faithfulness=judgement.score,
            judge_error=judgement.error,
            judge_usage=getattr(evaluator, "usage", None),
            judge_raw_message=getattr(evaluator, "raw", None),
        )
    except Exception as exc:  # noqa: BLE001 — retain the exact failing stage and terminal outcome
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["error_stage"] = stage
    finally:
        row["duration_ms"] = round((time.perf_counter() - started) * 1000)
    return row


def manifest_identity(dataset, profile, workflow, run_id, collection, triggered_by):
    identity = current_profile_identity(dataset)
    for key in ("top_k", "model_metadata", "dataset_hashes", "retrieval_config_hash"):
        if identity[key] != getattr(profile, key):
            raise ValueError(f"正式置信profile与当前{key}不一致")
    settings = get_settings()
    frozen = {
        "run_id": run_id,
        "collection": collection,
        "triggered_by": triggered_by,
        **identity,
        "confidence_hash": digest(asdict(profile)),
        "prompt_hashes": {"answer": digest(KNOWLEDGE_ANSWER_SYSTEM), "judge": digest(JUDGE_SYSTEM)},
        "generation_code_hash": digest(inspect.getsource(generate_knowledge_answer)),
        "evaluation_code_hash": digest(
            Path(__file__).read_text(encoding="utf-8").replace("\r\n", "\n")
        ),
        "judge_code_hash": digest(inspect.getsource(judge_answer)),
        "llm": {
            "model": settings.llm_model,
            "provider_hash": digest(settings.openai_base_url),
            "temperature": 0,
        },
        "package_versions": {
            name: importlib.metadata.version(name)
            for name in (
                "langchain-core",
                "langchain-openai",
                "langgraph",
                "pymilvus",
                "sentence-transformers",
                "sqlalchemy",
            )
        },
        "context_settings": workflow.settings.model_dump(mode="json"),
        "budget_profile": asdict(workflow.profile),
        "limits": asdict(workflow.limits),
        "rag_context_budget": context_ceiling(),
    }
    return {
        **frozen,
        "identity_hash": digest(frozen),
        "kind": "ch09_production_path_evaluation",
        "dataset_size": 40,
    }


async def prepare_corpus(dataset, workdir, collection, *, shared=None, resuming=False):
    corpus, cases = load_dataset(dataset / "corpus.jsonl", dataset / "queries.jsonl")
    corpus_dir = workdir / "corpus"
    corpus_dir.mkdir(exist_ok=True)
    factory, index = _factory(corpus_dir), _new_index(collection)
    if shared is None:
        from mewhelp.knowledge.embedding import embed_texts
        from mewhelp.knowledge.reranking import rerank_chunks

        embed, rerank = embed_texts, rerank_chunks
    else:
        embed, rerank = shared.embed, shared.rerank
    await settled_thread(index.ensure_collection)
    current = _snapshots(factory)
    expected = {source_id(d.source_key): _content_hash(d) for d in corpus}
    if any(s.id not in expected or s.content_hash != expected[s.id] for s in current):
        raise ValueError("隔离评估原文漂移，不能覆盖未知数据")
    with factory.begin() as db:
        for draft in corpus:
            put_chunk(db, draft)
    snapshots = _snapshots(factory)
    issues = await settled_thread(index.audit, snapshots)
    if issues:
        await settled_thread(reindex_all, factory, embed, index)
        issues = await settled_thread(index.audit, _snapshots(factory))
    if issues:
        raise RuntimeError("隔离原文与Milvus索引校验失败")
    return RetrievalRuntime(factory, embed, index, rerank), [c for c in cases if c.split == "test"]


async def evaluate_current_path(
    *,
    dataset,
    workdir,
    run_id,
    profile,
    resume=False,
    retrieval_runtime=None,
    workflow_context=None,
    on_progress=None,
    triggered_by="手动",
):
    dataset, workdir = Path(dataset), Path(workdir)
    collection = validate_run(run_id, workdir)
    workflow = workflow_context or WorkflowContext(None, get_ch05_model, None, AgentLimits())
    identity = await settled_thread(
        manifest_identity, dataset, profile, workflow, run_id, collection, triggered_by
    )
    _, all_cases = load_dataset(dataset / "corpus.jsonl", dataset / "queries.jsonl")
    case_ids = {c.id for c in all_cases if c.split == "test"}
    if len(case_ids) != 40:
        raise ValueError("必须恰好40条test")
    if resume:
        rows = resume_rows(workdir, identity, case_ids)
        old = json.loads((workdir / "manifest.json").read_text(encoding="utf-8"))
        resumed = list(old.get("resumes", [])) + [time.time()]
        started_at = old.get("started_at", time.time())
    else:
        workdir.mkdir(parents=True, exist_ok=False)
        rows = []
        resumed = []
        started_at = time.time()
    manifest = {
        **identity,
        "processed": len(rows),
        "status": "preparing",
        "started_at": started_at,
        "resumes": resumed,
    }
    atomic_json(workdir / "manifest.json", manifest)
    retrieval = None
    try:
        retrieval, cases = await prepare_corpus(
            dataset, workdir, collection, shared=retrieval_runtime, resuming=resume
        )
        isolated = replace(
            workflow,
            session_factory=retrieval.session_factory,
            rag_factory=None,
            tool_runtime=None,
            summary_manager=None,
            request_history=None,
            confidence_profile=profile,
        )
        manifest["status"] = "running"
        atomic_json(workdir / "manifest.json", manifest)
        done = {r["id"] for r in rows}
        if on_progress:
            on_progress(len(rows))
        for case in cases:
            if case.id in done:
                continue
            row = await evaluate_case(case, retrieval=retrieval, workflow=isolated, profile=profile)
            rows.append(row)
            atomic_rows(workdir / "cases.jsonl", rows)
            manifest["processed"] = len(rows)
            atomic_json(workdir / "manifest.json", manifest)
            if on_progress:
                on_progress(len(rows))
        metrics = summarize_rows(rows)
        complete = len(rows) == 40 and {r["id"] for r in rows} == case_ids
        status = (
            "completed_with_errors"
            if metrics["errors"] or metrics["judge_errors"]
            else "completed_all_na"
            if metrics["faithfulness_N"] == 0
            else "completed"
        )
        meta = {
            "run_id": run_id,
            "identity_hash": identity["identity_hash"],
            "result_hash": digest(rows),
            "status": status,
            "dataset_hashes": identity["dataset_hashes"],
            "model_metadata": identity["model_metadata"],
            "prompt_hashes": identity["prompt_hashes"],
            "confidence_hash": identity["confidence_hash"],
            "comparison_hash": digest(
                {
                    k: v
                    for k, v in identity.items()
                    if k not in {"run_id", "collection", "triggered_by", "identity_hash"}
                }
            ),
            "artifact_dir": str(workdir.resolve()),
            "elapsed_ms": round((time.time() - started_at) * 1000),
        }
        metrics["_meta"] = meta
        summary = {
            "dataset_size": 40,
            "processed": len(rows),
            "complete": complete,
            "status": status,
            "metrics": metrics,
        }
        atomic_json(workdir / "summary.json", summary)
        manifest.update(status=status, processed=len(rows), finished_at=time.time())
        atomic_json(workdir / "manifest.json", manifest)
        return summary
    except BaseException as exc:
        manifest.update(
            status="interrupted" if isinstance(exc, asyncio.CancelledError) else "error",
            error=f"{type(exc).__name__}: {exc}",
            processed=len(rows),
        )
        atomic_json(workdir / "manifest.json", manifest)
        raise
    finally:
        if retrieval:
            retrieval.session_factory.kw["bind"].dispose()
            # The index connection is owned by this evaluation; model callables are shared.
            retrieval.index.client.close()
