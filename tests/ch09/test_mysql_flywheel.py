import asyncio

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import sessionmaker

from mewhelp.ch09.dedup import DedupDecision
from mewhelp.ch09.flywheel import FlywheelWorker, process_gap
from mewhelp.ch09.locks import named_lock
from mewhelp.ch09.normalization import NormalizedGap
from mewhelp.db.models import ReviewQueue
from mewhelp.knowledge.refusals import LowConfidenceQuestion

pytestmark = pytest.mark.mysql


def seed(factory, n=1):
    with factory.begin() as db:
        rows = [
            LowConfidenceQuestion(
                original_question=f"问法{i}",
                entry_point="cli",
                trigger_stage="generation",
                reason_code="insufficient_evidence",
                reason="测试",
            )
            for i in range(n)
        ]
        db.add_all(rows)
        db.flush()
        return [r.id for r in rows]


async def normalize(original, **kwargs):
    await asyncio.sleep(0.02)
    return NormalizedGap(question="同一标准问题？", suggested_answer="待人工补充")


async def dedup(q, *, candidates):
    return DedupDecision(matched_ids=[i for i, _ in candidates], reason="测试同义")


def test_mysql_named_lock_excludes_another_connection_and_releases(ch09_mysql):
    with named_lock(ch09_mysql, scope="test") as first:
        assert first
        with named_lock(ch09_mysql, scope="test") as second:
            assert not second
    with named_lock(ch09_mysql, scope="test") as third:
        assert third


async def test_two_workers_merge_distinct_events_once(ch09_mysql):
    factory = sessionmaker(ch09_mysql, expire_on_commit=False)
    ids = seed(factory, 2)
    workers = [
        FlywheelWorker(factory, normalize=normalize, dedup=dedup, interval=0.02, retry_delay=0.03)
        for _ in range(2)
    ]
    for worker in workers:
        worker.start()
    try:
        for _ in range(150):
            await asyncio.sleep(0.02)
            with factory() as db:
                if all(db.get(LowConfidenceQuestion, i).matched_review_id for i in ids):
                    break
        with factory() as db:
            queue = db.scalars(select(ReviewQueue)).all()
            assert len(queue) == 1 and queue[0].occurrence_count == 2
            assert {db.get(LowConfidenceQuestion, i).matched_review_id for i in ids} == {
                queue[0].id
            }
    finally:
        for worker in workers:
            await worker.aclose()


async def test_mysql_atomic_merge_rollback_and_review_race(ch09_mysql):
    factory = sessionmaker(ch09_mysql, expire_on_commit=False)
    pid = seed(factory)[0]
    with factory.begin() as db:
        db.add(ReviewQueue(id=1, normalized_question="同义候选"))

    def fail(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("UPDATE LOW_CONFIDENCE_QUESTIONS"):
            raise RuntimeError("injected matched_review write failure")

    event.listen(ch09_mysql, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError):
            await process_gap(
                pid, factory=factory, engine=ch09_mysql, normalize=normalize, dedup=dedup
            )
    finally:
        event.remove(ch09_mysql, "before_cursor_execute", fail)
    with factory() as db:
        assert db.get(ReviewQueue, 1).occurrence_count == 1
        assert db.get(LowConfidenceQuestion, pid).matched_review_id is None

    async def race(q, *, candidates):
        with factory.begin() as db:
            db.get(ReviewQueue, 1).review_status = "驳回"
        return DedupDecision(matched_ids=["1"], reason="审核竞争")

    result = await process_gap(
        pid, factory=factory, engine=ch09_mysql, normalize=normalize, dedup=race
    )
    assert result != "1"
    with factory() as db:
        assert db.get(ReviewQueue, 1).occurrence_count == 1
    assert (
        await process_gap(pid, factory=factory, engine=ch09_mysql, normalize=normalize, dedup=dedup)
        == result
    )
