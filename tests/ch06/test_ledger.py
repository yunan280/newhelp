import pytest
from sqlalchemy import select

from mewhelp.db.models import Conversation, Message, MsgRole
from mewhelp.db.repository import TurnMessage


def test_committed_rows_can_retry_after_checkpoint_failure(session_factory):
    from mewhelp.db import repository
    assert hasattr(repository, "append_messages_once"), "missing idempotent ledger"
    with session_factory() as db:
        conv = Conversation(session_id="ledger", user_id="demo-user")
        db.add(conv); db.commit(); cid = conv.id
    rows = [TurnMessage(role=MsgRole.user, content="原话", ch06_event_key="a" * 64),
            TurnMessage(role=MsgRole.assistant, content="等待", ch06_event_key="b" * 64)]
    for _ in range(2):
        with session_factory() as db:
            repository.append_messages_once(db, conversation_id=cid, rows=rows)
            db.commit()
    with session_factory() as db:
        assert len(db.scalars(select(Message)).all()) == 2
        with pytest.raises(ValueError, match="idempotency"):
            repository.append_messages_once(db, conversation_id=cid, rows=[
                TurnMessage(role=MsgRole.user, content="被改的原话", ch06_event_key="a" * 64)])


def test_ledger_respects_outer_rollback(session_factory):
    from mewhelp.db.repository import append_messages_once
    with session_factory() as db:
        conv = Conversation(session_id="rollback", user_id="demo-user")
        db.add(conv); db.commit(); cid = conv.id
    with session_factory() as db:
        append_messages_once(db, conversation_id=cid, rows=[
            TurnMessage(role=MsgRole.user, content="不能提前提交", ch06_event_key="c" * 64)])
        db.rollback()
    with session_factory() as db:
        assert db.scalars(select(Message)).all() == []
