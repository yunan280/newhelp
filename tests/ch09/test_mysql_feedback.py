import asyncio

import pytest
from feedback_helpers import seed_answer
from sqlalchemy import event, select, text
from sqlalchemy.orm import sessionmaker

from mewhelp.ch09.feedback import FeedbackRequest, submit_negative_feedback
from mewhelp.db.models import Message, MsgRole
from mewhelp.db.repository import TurnMessage, append_messages_once
from mewhelp.knowledge.refusals import LowConfidenceQuestion, PoolCommitError

pytestmark = pytest.mark.mysql


def seeded(engine):
    with engine.begin() as db:
        db.execute(text("INSERT INTO conversations(id,session_id,user_id) VALUES(1,'s','u')"))
    factory = sessionmaker(engine, expire_on_commit=False)
    snapshot = seed_answer(factory, 1)
    request = FeedbackRequest(user_id="u", session_id="s", answer_message_id="11", choice="down")
    return factory, snapshot, request


async def test_two_connections_submit_once_and_ledger_replay_preserves_marker(ch09_mysql):
    factory, snapshot, request = seeded(ch09_mysql)
    receipts = await asyncio.gather(
        *(submit_negative_feedback(factory, request, recover=None) for _ in range(2))
    )
    assert receipts[0].pool_id == receipts[1].pool_id
    assert sorted(r.replayed for r in receipts) == [False, True]
    with factory.begin() as db:
        answer = db.get(Message, 11)
        append_messages_once(
            db,
            conversation_id=1,
            rows=[
                TurnMessage(
                    role=MsgRole.assistant,
                    content=answer.content,
                    ch06_event_key=answer.ch06_event_key,
                    retrieval_snapshot=snapshot.model_dump(mode="json"),
                )
            ],
        )
    with factory() as db:
        assert len(db.scalars(select(LowConfidenceQuestion)).all()) == 1
        assert db.get(Message, 11).retrieval_snapshot["feedback_lcq_id"] == receipts[0].pool_id


async def test_mysql_marker_failure_rolls_back_pool_and_allows_retry(ch09_mysql):
    factory, _, request = seeded(ch09_mysql)

    def fail(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("UPDATE MESSAGES"):
            raise RuntimeError("injected transaction failure")

    event.listen(ch09_mysql, "before_cursor_execute", fail)
    try:
        with pytest.raises(PoolCommitError):
            await submit_negative_feedback(factory, request, recover=None)
    finally:
        event.remove(ch09_mysql, "before_cursor_execute", fail)
    with factory() as db:
        assert db.scalar(select(LowConfidenceQuestion)) is None
        assert db.get(Message, 11).retrieval_snapshot["feedback_lcq_id"] is None
    assert not (await submit_negative_feedback(factory, request, recover=None)).replayed
