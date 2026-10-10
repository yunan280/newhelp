"""Durable unmatched rows are the queue; diagnostics describe this service run."""

import asyncio
import time
from contextlib import asynccontextmanager

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError

from mewhelp.ch07.budget import ContextBudgetError
from mewhelp.db.models import ReviewQueue
from mewhelp.knowledge.refusals import LowConfidenceQuestion, utc_now

from .contracts import EvidenceSnapshot, RequestTraceContext
from .dedup import DedupDecision
from .locks import named_lock
from .normalization import NormalizedGap
from .observability import get_observation_runtime
from .threads import settled_thread


class LockBusy(TimeoutError):
    pass


@asynccontextmanager
async def queue_lock(engine):
    lock = named_lock(engine, scope="review-queue")
    entry = asyncio.create_task(asyncio.to_thread(lock.__enter__))
    try:
        acquired = await asyncio.shield(entry)
    except asyncio.CancelledError:
        await entry
        await asyncio.to_thread(lock.__exit__, None, None, None)
        raise
    try:
        if not acquired:
            raise LockBusy("其他工作器正在处理待审队列")
        yield
    finally:
        await asyncio.to_thread(lock.__exit__, None, None, None)


def read_gap(factory, pool_id):
    with factory() as db:
        row = db.get(LowConfidenceQuestion, pool_id)
        if row is None:
            raise LookupError("问题池记录不存在")
        return row.original_question, row.retrieved_chunks, row.matched_review_id


def page_candidates(factory, after, size):
    with factory() as db:
        return [
            (str(i), q)
            for i, q in db.execute(
                select(ReviewQueue.id, ReviewQueue.normalized_question)
                .where(ReviewQueue.review_status == "待审", ReviewQueue.id > after)
                .order_by(ReviewQueue.id)
                .limit(size)
            )
        ]


def commit_match(factory, pool_id, gap, matched):
    with factory.begin() as db:
        row = db.scalar(
            select(LowConfidenceQuestion)
            .where(LowConfidenceQuestion.id == pool_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None:
            raise LookupError("问题池记录不存在")
        if row.matched_review_id:
            return str(row.matched_review_id)
        if matched:
            target = db.scalar(
                select(ReviewQueue)
                .where(ReviewQueue.id == int(matched))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if target is None or target.review_status != "待审":
                return None
            db.execute(
                update(ReviewQueue)
                .where(ReviewQueue.id == target.id)
                .values(occurrence_count=ReviewQueue.occurrence_count + 1, updated_at=utc_now())
            )
            review_id = target.id
        else:
            target = ReviewQueue(
                normalized_question=gap.question,
                ai_suggested_answer=gap.suggested_answer,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            db.add(target)
            db.flush()
            review_id = target.id
        row.matched_review_id = review_id
        return str(review_id)


async def process_gap(pool_id, *, factory, engine, normalize, dedup):
    async with queue_lock(engine):
        original, raw, matched = await asyncio.to_thread(read_gap, factory, pool_id)
        if matched:
            return str(matched)
        snapshot = EvidenceSnapshot.model_validate(raw) if raw else None
        gap = NormalizedGap.model_validate(await normalize(original, snapshot=snapshot))
        for _ in range(3):
            after = 0
            matches = set()
            size = 8
            while True:
                candidates = await asyncio.to_thread(page_candidates, factory, after, size)
                if not candidates:
                    break
                try:
                    decision = DedupDecision.model_validate(
                        await dedup(gap.question, candidates=candidates)
                    )
                except ContextBudgetError:
                    if size == 1:
                        raise
                    size = max(1, size // 2)
                    continue
                if not set(decision.matched_ids) <= {i for i, _ in candidates}:
                    raise ValueError("模型返回候选页外ID")
                matches.update(decision.matched_ids)
                after = int(candidates[-1][0])
            chosen = min(matches, key=int) if matches else None
            result = await settled_thread(commit_match, factory, pool_id, gap, chosen)
            if result:
                return result
        raise LockBusy("候选审核状态连续变化，请重试")


def is_transient(exc):
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, OperationalError):
        return getattr(exc.orig, "args", [None])[0] in {1205, 1213, 2003, 2006, 2013}
    # Official provider/network errors expose status_code; invalid schemas are permanent.
    return getattr(exc, "status_code", None) in {408, 429, 500, 502, 503, 504} or type(
        exc
    ).__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectError",
        "ReadTimeout",
        "ConnectTimeout",
    }


class FlywheelWorker:
    def __init__(self, factory, *, normalize, dedup, interval=10, retry_delay=5, observations=None):
        self.factory = factory
        self.engine = factory.kw["bind"]
        self.normalize = normalize
        self.dedup = dedup
        self.interval = interval
        self.retry_delay = retry_delay
        self.observations = observations or get_observation_runtime()
        self.attempts = {}
        self.event = asyncio.Event()
        self.task = None
        self.scan_error = None

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self.run(), name="ch09-flywheel")

    def wake(self):
        self.event.set()

    def retry(self, pool_id):
        self.attempts.pop(str(pool_id), None)
        self.wake()

    def pending(self):
        with self.factory() as db:
            return list(
                db.scalars(
                    select(LowConfidenceQuestion.id)
                    .where(LowConfidenceQuestion.matched_review_id.is_(None))
                    .order_by(LowConfidenceQuestion.id)
                )
            )

    def status(self):
        try:
            count = len(self.pending())
            self.scan_error = None
        except Exception as exc:  # noqa: BLE001 — diagnostics must report any failed scan
            count = None
            self.scan_error = f"{type(exc).__name__}: {exc}"[:512]
        return {
            "pending_count": count,
            "scan_error": self.scan_error,
            "attempts": {k: dict(v) for k, v in self.attempts.items()},
            "diagnostics_scope": "当前服务运行；原话和归并结果持久化，尝试历史不持久化",
        }

    async def run(self):
        while True:
            self.event.clear()
            try:
                ids = await asyncio.to_thread(self.pending)
                self.scan_error = None
            except Exception as exc:  # noqa: BLE001 — preserve durable work across failed scans
                self.scan_error = f"{type(exc).__name__}: {exc}"[:512]
                ids = []
            for pid in ids:
                old = self.attempts.get(str(pid), {})
                if (
                    old.get("state") == "manual_retry"
                    or old.get("next_attempt_at", 0) > time.monotonic()
                ):
                    continue
                attempt = old.get("count", 0) + 1
                self.attempts[str(pid)] = {"count": attempt, "state": "running"}
                try:
                    ctx = RequestTraceContext(entry_point="flywheel", trace_kind="flywheel")
                    with self.observations.request(ctx, input={"pool_id": str(pid)}) as root:
                        review = await process_gap(
                            pid,
                            factory=self.factory,
                            engine=self.engine,
                            normalize=self.normalize,
                            dedup=self.dedup,
                        )
                        root.finish(status="completed", output={"review_id": review})
                    self.attempts[str(pid)] = {
                        "count": attempt,
                        "state": "completed",
                        "review_id": review,
                    }
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — classify and expose each poison row
                    retry = is_transient(exc) and attempt < 3
                    self.attempts[str(pid)] = {
                        "count": attempt,
                        "state": "retry_pending" if retry else "manual_retry",
                        "error": f"{type(exc).__name__}: {exc}"[:512],
                        "next_attempt_at": time.monotonic() + self.retry_delay * 2 ** (attempt - 1)
                        if retry
                        else None,
                    }
            try:
                await asyncio.wait_for(self.event.wait(), timeout=self.interval)
            except TimeoutError:
                pass

    async def aclose(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
            self.task = None
