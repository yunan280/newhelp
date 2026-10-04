from importlib import import_module

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from mewhelp.ch07.config import BudgetProfile
from mewhelp.db.base import Base
from mewhelp.db.models import Conversation, Message, MsgRole


def modules():
    try:
        return import_module('mewhelp.ch07.store'), import_module('mewhelp.ch07.types')
    except ModuleNotFoundError:
        pytest.fail('Ch07 needs conversation-local anchors and atomic append-only summaries')


@pytest.fixture
def factory(tmp_path):
    modules()
    engine = create_engine(f'sqlite:///{tmp_path / "db.sqlite"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add_all([Conversation(id=1, session_id='one', user_id='alice'),
                         Conversation(id=2, session_id='two', user_id='bob')])
        session.flush()
        for ident, conv, role in [(10, 1, MsgRole.user), (11, 2, MsgRole.user),
                                  (14, 1, MsgRole.assistant), (22, 2, MsgRole.assistant),
                                  (23, 1, MsgRole.user), (29, 1, MsgRole.assistant)]:
            session.add(Message(id=ident, conversation_id=conv, role=role, content=str(ident)))
        session.get(Conversation, 1).layer1_from_msg_id = 29
    yield factory
    engine.dispose()


def job(types, old=0, upto=14, start=10):
    return types.SummaryJob(1, old, upto, (types.HistoryTurn('t', start, upto,
        (HumanMessage('订单1001，询问物流'), AIMessage('待查询'))),))


def test_noncontiguous_ids_stay_conversation_local(factory):
    store, _ = modules()
    with factory() as session:
        snapshot = store.read_conversation(session, conversation_id=1, user_id='alice')
        assert [m.id for m in snapshot.messages] == [10, 14, 23, 29]
        with pytest.raises(LookupError):
            store.read_conversation(session, conversation_id=1, user_id='bob')


def test_summary_commit_is_atomic(factory):
    store, types = modules()
    with factory() as session:
        segment = store.append_summary(session, job=job(types), from_msg_id=10,
            upto_msg_id=14, content='订单1001物流待查询', profile=BudgetProfile())
        assert segment.seq == 1
        session.rollback()
    with factory() as session:
        snapshot = store.read_conversation(session, conversation_id=1, user_id='alice')
        assert snapshot.summaries == () and snapshot.summary_upto_msg_id == 0
        assert session.get(Conversation, 1).summary is None


def test_stale_snapshot_skips_and_preserves_advanced_layer1(factory):
    store, types = modules()
    with factory.begin() as session:
        first = store.append_summary(session, job=job(types), from_msg_id=10,
            upto_msg_id=14, content='订单1001物流待查询', profile=BudgetProfile())
        assert first.seq == 1
    with factory.begin() as session:
        assert store.append_summary(session, job=job(types), from_msg_id=10,
            upto_msg_id=14, content='不能改写', profile=BudgetProfile()) is None
    with factory() as session:
        snapshot = store.read_conversation(session, conversation_id=1, user_id='alice')
        assert snapshot.layer1_from_msg_id == 29 and snapshot.summary_upto_msg_id == 14
        assert [s.content for s in snapshot.summaries] == ['订单1001物流待查询']


def test_cannot_skip_unsummarized_range(factory):
    store, types = modules()
    with factory.begin() as session, pytest.raises(ValueError, match='range'):
        store.append_summary(session, job=job(types, upto=29, start=23),
            from_msg_id=23, upto_msg_id=29, content='漏掉第一轮', profile=BudgetProfile())


def test_latest_projection_keeps_whole_segments(factory):
    store, types = modules()
    profile = BudgetProfile(summary_reserve=60)
    with factory.begin() as session:
        store.append_summary(session, job=job(types), from_msg_id=10, upto_msg_id=14,
                             content='旧' * 40, profile=profile)
        store.append_summary(session, job=job(types, old=14, upto=29, start=23),
            from_msg_id=23, upto_msg_id=29, content='新' * 40, profile=profile)
    with factory() as session:
        assert session.get(Conversation, 1).summary == '新' * 40
        assert len(store.read_conversation(session, conversation_id=1,
                                          user_id='alice').summaries) == 2


def test_layer1_compare_and_swap(factory):
    store, _ = modules()
    with factory.begin() as session:
        assert not store.advance_layer1(session, conversation_id=1,
                                        expected_layer1=0, new_layer1=29)
        assert not store.advance_layer1(session, conversation_id=1,
                                        expected_layer1=29, new_layer1=14)
    with factory() as session:
        assert session.scalar(select(Conversation.layer1_from_msg_id)) == 29
