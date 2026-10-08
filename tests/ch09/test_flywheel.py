import asyncio
from contextlib import contextmanager

import pytest

from mewhelp.db.models import ReviewQueue
from mewhelp.knowledge.refusals import LowConfidenceQuestion


@pytest.fixture
def lock(monkeypatch):
    from mewhelp.ch09 import flywheel

    @contextmanager
    def fake(*args, **kwargs):
        yield True

    monkeypatch.setattr(flywheel, "named_lock", fake)


def pool(factory, question="原问题"):
    with factory.begin() as db:
        row = LowConfidenceQuestion(
            original_question=question,
            entry_point="cli",
            trigger_stage="generation",
            reason_code="insufficient_evidence",
            reason="标注缺口",
        )
        db.add(row)
        db.flush()
        return row.id


async def normalized(original, **kwargs):
    from mewhelp.ch09.normalization import NormalizedGap

    return NormalizedGap(question=original + "？", suggested_answer="待人工核准：知识库暂缺依据。")


async def test_match_in_second_page_and_smallest_numeric_id(ch09_db, lock):
    from mewhelp.ch09.dedup import DedupDecision
    from mewhelp.ch09.flywheel import process_gap

    with ch09_db.begin() as db:
        db.add_all(ReviewQueue(id=i, normalized_question=str(i)) for i in range(1, 12))
    pid = pool(ch09_db)
    pages = []

    async def dedup(q, *, candidates):
        pages.append(candidates)
        return DedupDecision(
            matched_ids=[i for i, _ in candidates if i in {"9", "10"}], reason="同义"
        )

    result = await process_gap(
        pid, factory=ch09_db, engine=ch09_db.kw["bind"], normalize=normalized, dedup=dedup
    )
    replay = await process_gap(
        pid, factory=ch09_db, engine=ch09_db.kw["bind"], normalize=normalized, dedup=dedup
    )
    assert result == replay == "9" and len(pages) == 2 and all(len(p) <= 8 for p in pages)
    with ch09_db() as db:
        assert db.get(ReviewQueue, 9).occurrence_count == 2
        assert db.get(ReviewQueue, 10).occurrence_count == 1


async def test_invalid_id_never_updates_queue(ch09_db, lock):
    from mewhelp.ch09.dedup import DedupDecision
    from mewhelp.ch09.flywheel import process_gap

    pid = pool(ch09_db)
    with ch09_db.begin() as db:
        db.add(ReviewQueue(id=1, normalized_question="问题"))

    async def dedup(q, *, candidates):
        return DedupDecision(matched_ids=["999"], reason="越界")

    with pytest.raises(ValueError, match="候选"):
        await process_gap(
            pid, factory=ch09_db, engine=ch09_db.kw["bind"], normalize=normalized, dedup=dedup
        )
    with ch09_db() as db:
        assert db.get(LowConfidenceQuestion, pid).matched_review_id is None
        assert db.get(ReviewQueue, 1).occurrence_count == 1


async def test_review_becomes_terminal_during_dedup(ch09_db, lock):
    from mewhelp.ch09.dedup import DedupDecision
    from mewhelp.ch09.flywheel import process_gap

    pid = pool(ch09_db)
    with ch09_db.begin() as db:
        db.add(ReviewQueue(id=1, normalized_question="问题"))

    async def dedup(q, *, candidates):
        with ch09_db.begin() as db:
            db.get(ReviewQueue, 1).review_status = "通过"
        return DedupDecision(matched_ids=["1"], reason="同义")

    result = await process_gap(
        pid, factory=ch09_db, engine=ch09_db.kw["bind"], normalize=normalized, dedup=dedup
    )
    assert result != "1"
    with ch09_db() as db:
        assert db.get(ReviewQueue, 1).occurrence_count == 1


async def test_poison_row_does_not_starve_next_and_manual_retry(ch09_db, lock):
    from mewhelp.ch09.dedup import DedupDecision
    from mewhelp.ch09.flywheel import FlywheelWorker

    poison = pool(ch09_db, "坏")
    good = pool(ch09_db, "好")
    fixed = False

    async def normalize(original, **kwargs):
        if original == "坏" and not fixed:
            raise ValueError("schema invalid")
        return await normalized(original)

    async def dedup(q, **kwargs):
        return DedupDecision(matched_ids=[], reason="不同")

    worker = FlywheelWorker(ch09_db, normalize=normalize, dedup=dedup, interval=0.01)
    worker.start()
    try:
        for _ in range(80):
            await asyncio.sleep(0.01)
            with ch09_db() as db:
                if db.get(LowConfidenceQuestion, good).matched_review_id:
                    break
        assert worker.status()["attempts"][str(poison)]["state"] == "manual_retry"
        with ch09_db() as db:
            assert db.get(LowConfidenceQuestion, good).matched_review_id
        fixed = True
        worker.retry(poison)
        for _ in range(80):
            await asyncio.sleep(0.01)
            with ch09_db() as db:
                if db.get(LowConfidenceQuestion, poison).matched_review_id:
                    break
        with ch09_db() as db:
            assert db.get(LowConfidenceQuestion, poison).matched_review_id
    finally:
        await worker.aclose()


async def test_transient_three_attempts_then_stop_and_shutdown_preserves_pool(ch09_db, lock):
    from mewhelp.ch09.flywheel import FlywheelWorker

    pid = pool(ch09_db)
    calls = 0

    async def normalize(original, **kwargs):
        nonlocal calls
        calls += 1
        raise TimeoutError("temporary")

    async def dedup(q, **kwargs):
        raise AssertionError("must not call")

    worker = FlywheelWorker(
        ch09_db, normalize=normalize, dedup=dedup, interval=0.01, retry_delay=0.01
    )
    worker.start()
    try:
        for _ in range(100):
            await asyncio.sleep(0.01)
            if calls == 3 and worker.status()["attempts"][str(pid)]["state"] == "manual_retry":
                break
        assert calls == 3
        await asyncio.sleep(0.03)
        assert calls == 3
    finally:
        await worker.aclose()
    with ch09_db() as db:
        assert db.get(LowConfidenceQuestion, pid).matched_review_id is None


async def test_cancellation_leaves_durable_gap_for_next_start(ch09_db, lock):
    from mewhelp.ch09.flywheel import FlywheelWorker

    pid = pool(ch09_db)
    waiting = asyncio.Event()

    async def normalize(original, **kwargs):
        waiting.set()
        await asyncio.Event().wait()

    worker = FlywheelWorker(ch09_db, normalize=normalize, dedup=None, interval=0.01)
    worker.start()
    await asyncio.wait_for(waiting.wait(), 2)
    await worker.aclose()
    with ch09_db() as db:
        assert db.get(LowConfidenceQuestion, pid).matched_review_id is None


async def test_dedup_prompt_excludes_answers_and_rejects_unknown_ids():
    from mewhelp.ch09.dedup import find_equivalent
    from mewhelp.ch09.normalization import NormalizedGap

    class Model:
        def with_structured_output(self, schema, **kwargs):
            self.schema = schema
            return self

        async def ainvoke(self, messages):
            assert "只有候选问题" in messages[0].content
            return {"parsed": {"matched_ids": ["999"], "reason": "无效"}, "parsing_error": None}

    with pytest.raises(ValueError, match="候选"):
        await find_equivalent("问题", candidates=[("9", "候选问题")], model=Model())
    with pytest.raises(ValueError):
        NormalizedGap(question=" ", suggested_answer="待核准")


async def test_page_budget_shrinks_without_skipping_candidates(ch09_db, lock):
    from mewhelp.ch07.budget import ContextBudgetError
    from mewhelp.ch09.dedup import DedupDecision
    from mewhelp.ch09.flywheel import process_gap

    pid = pool(ch09_db)
    with ch09_db.begin() as db:
        db.add_all(ReviewQueue(id=i, normalized_question=str(i)) for i in range(1, 10))
    visited = []

    async def dedup(q, *, candidates):
        if len(candidates) > 2:
            raise ContextBudgetError("page exceeds window")
        visited.extend(i for i, _ in candidates)
        return DedupDecision(matched_ids=[], reason="不同")

    await process_gap(
        pid, factory=ch09_db, engine=ch09_db.kw["bind"], normalize=normalized, dedup=dedup
    )
    assert visited == [str(i) for i in range(1, 10)]


async def test_scan_failure_is_visible_not_an_empty_queue(ch09_db, lock):
    from mewhelp.ch09.flywheel import FlywheelWorker

    worker = FlywheelWorker(ch09_db, normalize=normalized, dedup=None, interval=0.01)

    def broken():
        raise ConnectionError("database unavailable")

    worker.pending = broken
    worker.start()
    try:
        await asyncio.sleep(0.03)
        status = worker.status()
        assert status["pending_count"] is None and "database unavailable" in status["scan_error"]
    finally:
        await worker.aclose()
