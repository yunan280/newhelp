import asyncio
from importlib import import_module

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mewhelp.ch07.config import BudgetProfile, ContextSettings
from mewhelp.ch07.store import read_conversation
from mewhelp.ch07.types import HistoryTurn, SummaryJob, SummaryResult
from mewhelp.db.base import Base
from mewhelp.db.models import Conversation, Message, MsgRole


def manager_type():
    try:
        return import_module('mewhelp.ch07.summary').SummaryTaskManager
    except ModuleNotFoundError:
        pytest.fail('Ch07 must schedule a deduplicated background summary without blocking replies')


@pytest.fixture
def factory(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "summary.sqlite"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add_all([Conversation(id=1, session_id='s1', user_id='alice', layer1_from_msg_id=14),
                         Conversation(id=2, session_id='s2', user_id='bob', layer1_from_msg_id=34)])
        session.flush()
        for ident, conv, role in [(10, 1, MsgRole.user), (14, 1, MsgRole.assistant),
                                  (20, 1, MsgRole.user), (24, 1, MsgRole.assistant),
                                  (30, 2, MsgRole.user), (34, 2, MsgRole.assistant)]:
            session.add(Message(id=ident, conversation_id=conv, role=role,
                                content='订单1001物流未解决'))
    yield factory
    engine.dispose()


def job(conv=1):
    start, end = (10, 14) if conv == 1 else (30, 34)
    return SummaryJob(conv, 0, end, (HistoryTurn(f't{conv}', start, end,
        (HumanMessage('订单1001查物流'), AIMessage('尚未查到结果'))),))


class HeldModel:
    def __init__(self, fail=False):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.fail = fail
        self.backgrounds = []

    async def summarize(self, *, batch, background):
        self.backgrounds.append(background)
        self.started.set()
        await self.release.wait()
        if self.fail:
            raise RuntimeError('upstream failed')
        return SummaryResult('用户询问订单1001的物流进展。客服本批尚未查询到物流结果，'
                             '用户提出的查询诉求仍未解决，目前没有可确认的派送结论。',
                             {'input_tokens': 80, 'output_tokens': 60}, 1)


async def test_inflight_summary_does_not_block_foreground_and_deduplicates(factory):
    model = HeldModel()
    manager = manager_type()(factory, model, BudgetProfile())
    async def foreground():
        assert manager.schedule(job())
        assert not manager.schedule(job())
        return '当前轮已回复'
    assert await asyncio.wait_for(foreground(), .1) == '当前轮已回复'
    await asyncio.wait_for(model.started.wait(), 1)
    with factory() as session:
        assert read_conversation(session, conversation_id=1, user_id='alice').summary_upto_msg_id == 0
    model.release.set()
    await manager.aclose()
    with factory() as session:
        assert read_conversation(session, conversation_id=1, user_id='alice').summary_upto_msg_id == 14


async def test_advance_during_model_call_commits_only_snapshot(factory):
    model = HeldModel()
    manager = manager_type()(factory, model, BudgetProfile())
    manager.schedule(job())
    await asyncio.wait_for(model.started.wait(), 1)
    with factory.begin() as session:
        session.get(Conversation, 1).layer1_from_msg_id = 24
    model.release.set()
    await manager.aclose()
    with factory() as session:
        snapshot = read_conversation(session, conversation_id=1, user_id='alice')
        assert (snapshot.summary_upto_msg_id, snapshot.layer1_from_msg_id) == (14, 24)
        assert snapshot.summaries[0].upto_msg_id == 14


async def test_failure_leaves_anchors_and_releases_inflight(factory):
    model = HeldModel(fail=True)
    manager = manager_type()(factory, model, BudgetProfile())
    manager.schedule(job())
    await model.started.wait()
    model.release.set()
    await manager.aclose()
    with factory() as session:
        snapshot = read_conversation(session, conversation_id=1, user_id='alice')
        assert snapshot.summary_upto_msg_id == 0 and snapshot.summaries == ()
    assert not manager.inflight


async def test_shutdown_cancels_without_advancing_coverage(factory):
    model = HeldModel()
    manager = manager_type()(factory, model, BudgetProfile())
    manager.schedule(job())
    await model.started.wait()
    await manager.aclose(timeout_seconds=.01)
    assert not manager.inflight
    with factory() as session:
        assert session.get(Conversation, 1).summary_upto_msg_id is None


async def test_two_conversations_do_not_share_background(factory):
    model = HeldModel()
    model.release.set()
    manager = manager_type()(factory, model, BudgetProfile(), concurrency=2)
    assert manager.schedule(job()) and manager.schedule(job(2))
    await manager.aclose()
    with factory() as session:
        assert len(read_conversation(session, conversation_id=1, user_id='alice').summaries) == 1
        assert len(read_conversation(session, conversation_id=2, user_id='bob').summaries) == 1
    assert model.backgrounds == ['', '']


async def test_large_batch_appends_each_range_without_refeeding_new_summary(factory):
    model = HeldModel()
    model.release.set()
    with factory.begin() as session:
        session.get(Conversation, 1).layer1_from_msg_id = 24
    turns = tuple(HistoryTurn(f't{i}', start, end, (
        HumanMessage('订单1001物流：' + '原' * 700), AIMessage('待查询' * 20)))
        for i, (start, end) in enumerate([(10, 14), (20, 24)]))
    manager = manager_type()(factory, model, BudgetProfile(),
                            settings=ContextSettings(model_context_window=3000, _env_file=None))
    manager.schedule(SummaryJob(1, 0, 24, turns))
    await manager.aclose()
    with factory() as session:
        snapshot = read_conversation(session, conversation_id=1, user_id='alice')
        assert [(s.from_msg_id, s.upto_msg_id) for s in snapshot.summaries] == [(10, 14), (20, 24)]
        assert snapshot.summary_upto_msg_id == 24
    assert model.backgrounds == ['', '']


async def test_competing_managers_cannot_duplicate_or_rewrite_segment(factory, monkeypatch):
    import threading
    from sqlalchemy.orm import Session
    barrier = threading.Barrier(2)
    committed = threading.Event()
    local = threading.local()
    class RacingSession(Session):
        def scalar(self, statement, *args, **kwargs):
            result = super().scalar(statement, *args, **kwargs)
            if 'FROM conversations' in str(statement):
                local.second = barrier.wait(timeout=3) == 0
            return result
        def scalars(self, statement, *args, **kwargs):
            if 'FROM conversation_summaries' in str(statement) and getattr(local, 'second', False):
                assert committed.wait(timeout=3)
            return super().scalars(statement, *args, **kwargs)
    racing_factory = sessionmaker(factory.kw['bind'], class_=RacingSession, expire_on_commit=False)
    original = manager_type()._commit
    def commit(manager, *args):
        result = original(manager, *args)
        if not local.second:
            committed.set()
        return result
    monkeypatch.setattr(manager_type(), '_commit', commit)
    model = HeldModel()
    model.release.set()
    managers = [manager_type()(racing_factory, model, BudgetProfile()) for _ in range(2)]
    for manager in managers:
        manager.schedule(job())
    await asyncio.gather(*(m.aclose() for m in managers))
    with factory() as session:
        snapshot = read_conversation(session, conversation_id=1, user_id='alice')
        assert len(snapshot.summaries) == 1 and snapshot.summaries[0].seq == 1
