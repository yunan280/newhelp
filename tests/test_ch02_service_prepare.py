"""_prepare_turn —— 会话身份 + 上下文组装 + turn1 定工具 + 执行。

这一层不落库、不收敛、不加锁、不生成 id,所以能单独测。
生成 id 与加锁由两个出口负责,见 Task 14 / 15。
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mewhelp.ch02 import service
from mewhelp.db.base import Base
from mewhelp.db.models import Conversation
from mewhelp.db.seed import seed
from tests.fakes import FakeToolChatModel, text_chunks, tool_call_chunks


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        seed(s)
        s.commit()
    return lambda: Session(engine)


def patch_model(monkeypatch, model) -> FakeToolChatModel:
    monkeypatch.setattr(service, "get_chat_model", lambda **kw: model)
    return model


# ---------- 会话身份三态 ----------


async def test_resumes_an_existing_session(session_factory, monkeypatch):
    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("好的")] * 2))
    first = await service._prepare_turn(
        session_factory, session_id="s1", user_id="u1", message="第一问"
    )
    second = await service._prepare_turn(
        session_factory, session_id="s1", user_id="u1", message="第二问"
    )

    assert first.resumed is False
    assert second.resumed is True
    assert second.conversation_id == first.conversation_id


async def test_creates_the_conversation_for_an_unknown_session_id(session_factory, monkeypatch):
    """带了但查不到 → 按它建,resumed=False(spec §12.3)。

    返回 404 会把客户端卡在一个它无法自救的状态 —— 它手上没有别的 id 可用。
    但"静默"不行,所以 resumed 要把这件事说出来。
    """
    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("您好")]))
    prepared = await service._prepare_turn(
        session_factory, session_id="never-seen", user_id="u1", message="在吗"
    )

    assert prepared.resumed is False
    with session_factory() as s:
        assert s.scalars(select(Conversation)).one().session_id == "never-seen"


# ---------- turn1 定工具 ----------


async def test_binds_all_five_tools_on_the_first_call(session_factory, monkeypatch):
    model = patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("您好")]))
    await service._prepare_turn(session_factory, session_id="s1", user_id="u1", message="在吗")

    assert {t.name for t in model.bound_tools} == {
        "query_order", "query_product", "query_logistics", "query_faq", "create_ticket"
    }


async def test_collects_complete_tool_calls_from_streamed_chunks(session_factory, monkeypatch):
    """Review Focus:turn1 走 astream,但**必须**拿到完整的 tool_calls。

    这是方案 (c) 成立的前提 —— 实测 AIMessageChunk 相加能把 tool_call_chunks
    拼回完整对象。这条守的就是那个能力:把 astream 换成"只看最后一个 chunk",
    这条会红(最后一个 chunk 只有半截 args)。
    """
    model = FakeToolChatModel(rounds=[
        tool_call_chunks("query_logistics", '{"order_id": "1001"}'),
    ])
    patch_model(monkeypatch, model)

    prepared = await service._prepare_turn(
        session_factory, session_id="s1", user_id="u1", message="订单 1001 的物流到哪了"
    )

    assert prepared.ai.tool_calls[0]["name"] == "query_logistics"
    assert prepared.ai.tool_calls[0]["args"] == {"order_id": "1001"}


async def test_executes_every_tool_call_in_the_round(session_factory, monkeypatch):
    """一轮里两个 tool_calls → **两个都执行**(spec §11)。

    丢掉第二个等于模型说的话被静默截断,而用户看不到任何痕迹。
    """
    model = FakeToolChatModel(rounds=[
        tool_call_chunks("query_order", '{"order_id": "1001"}', call_id="c1", index=0)
        + tool_call_chunks("query_logistics", '{"order_id": "1001"}', call_id="c2", index=1),
    ])
    patch_model(monkeypatch, model)

    prepared = await service._prepare_turn(
        session_factory, session_id="s1", user_id="u1", message="订单 1001 的状态和物流"
    )

    assert [r.name for r in prepared.tool_results] == ["query_order", "query_logistics"]
    # 两个都要**真跑通**,不是"名字对了就行":index 给重的话第二个调用会拿到空 args,
    # 以 ok=False 的样子混过去 —— 名字断言照样绿。
    assert all(r.ok for r in prepared.tool_results)
    assert [r.args for r in prepared.tool_results] == [
        {"order_id": "1001"}, {"order_id": "1001"}
    ]


async def test_tool_failure_does_not_break_the_turn(session_factory, monkeypatch):
    """工具失败 → 一条 ok=False 的结果,回合继续。整轮不许 500。"""
    model = FakeToolChatModel(rounds=[tool_call_chunks("query_stock", '{"sku": "1"}')])
    patch_model(monkeypatch, model)

    prepared = await service._prepare_turn(
        session_factory, session_id="s1", user_id="u1", message="有货吗"
    )

    assert prepared.tool_results[0].ok is False
    assert prepared.tool_results[0].error == "unknown_tool"


async def test_a_plain_turn_has_no_tool_results(session_factory, monkeypatch):
    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("您好,有什么可以帮您?")]))
    prepared = await service._prepare_turn(
        session_factory, session_id="s1", user_id="u1", message="你好"
    )

    assert prepared.ai.tool_calls == []
    assert prepared.tool_results == []


# ---------- 上下文组装 ----------


async def test_replays_user_and_final_assistant_across_turns(session_factory, monkeypatch):
    """第一轮带上历史之后,第二轮的 messages 里要有第一轮的问答。"""
    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("第一答")] * 2))
    first = await service._prepare_turn(
        session_factory, session_id="s1", user_id="u1", message="第一问"
    )
    service.persist_turn(session_factory, first, answer="第一答")

    prepared = await service._prepare_turn(
        session_factory, session_id="s1", user_id="u1", message="第二问"
    )

    seen = [m.content for m in prepared.messages if m.type in ("human", "ai")]
    assert seen == ["第一问", "第一答", "第二问"]


async def test_does_not_replay_the_tool_call_round(session_factory, monkeypatch):
    """带 tool_calls 的 assistant 与 tool 消息**不**跨轮回放(spec §11)。

    回放它们会让模型看到自己上一轮的半截前言("让我查一下")。
    """
    model = FakeToolChatModel(rounds=[
        tool_call_chunks("query_logistics", '{"order_id": "1001"}'),
    ])
    patch_model(monkeypatch, model)
    first = await service._prepare_turn(
        session_factory, session_id="s1", user_id="u1", message="订单 1001 的物流"
    )
    # 只传 answer —— 工具那两条行是 persist_turn 自己从 prepared 里拼的
    service.persist_turn(session_factory, first, answer="您的包裹已到杭州。")

    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("第二答")]))
    prepared = await service._prepare_turn(
        session_factory, session_id="s1", user_id="u1", message="第二问"
    )

    seen = [m.content for m in prepared.messages if m.type in ("human", "ai")]
    assert seen == ["订单 1001 的物流", "您的包裹已到杭州。", "第二问"]


async def test_system_prompt_is_first(session_factory, monkeypatch):
    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("您好")]))
    prepared = await service._prepare_turn(
        session_factory, session_id="s1", user_id="u1", message="在吗"
    )

    assert prepared.messages[0].type == "system"
    assert "MewHelp" in prepared.messages[0].content
