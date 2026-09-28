"""非流式出口 —— eval / 脚本 / 单测的一眼看轨迹入口。"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mewhelp.ch02 import service
from mewhelp.db.base import Base
from mewhelp.db.models import Message, MsgRole
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


def patch_model(monkeypatch, model):
    monkeypatch.setattr(service, "get_chat_model", lambda **kw: model)
    return model


async def test_answers_without_tools(session_factory, monkeypatch):
    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("您好,有什么可以帮您?")]))
    result = await service.run_agent_turn(
        session_factory, session_id="s1", user_id="u1", message="你好"
    )

    assert result.answer == "您好,有什么可以帮您?"
    assert result.tool_calls == []
    assert result.tool_results == []
    assert result.resumed is False


async def test_returns_the_full_tool_trace(session_factory, monkeypatch):
    """这条出口的全部意义:一眼看到模型选了哪个工具、参数是什么、成没成。"""
    patch_model(monkeypatch, FakeToolChatModel(rounds=[
        tool_call_chunks("query_logistics", '{"order_id": "1001"}'),
        text_chunks("您的包裹已到杭州。"),
    ]))
    result = await service.run_agent_turn(
        session_factory, session_id="s1", user_id="u1", message="订单 1001 的物流到哪了"
    )

    assert result.answer == "您的包裹已到杭州。"
    assert [c["name"] for c in result.tool_calls] == ["query_logistics"]
    assert result.tool_calls[0]["args"] == {"order_id": "1001"}
    assert result.tool_results[0].ok is True
    assert "杭州" in result.tool_results[0].content


async def test_convergence_does_not_bind_tools(session_factory, monkeypatch):
    """与流式出口同一条硬约束:单轮。"""
    model = patch_model(monkeypatch, FakeToolChatModel(rounds=[
        tool_call_chunks("query_logistics", '{"order_id": "1001"}'),
        text_chunks("已到杭州。"),
    ]))
    await service.run_agent_turn(
        session_factory, session_id="s1", user_id="u1", message="订单 1001 的物流"
    )
    assert model.bind_calls == 1


async def test_convergence_uses_ainvoke_not_astream(session_factory, monkeypatch):
    """非流式出口的收敛用 ainvoke —— 拿一次性结果,没有逐 token 的必要。

    判别力:两个出口共用 _prepare_turn,turn1 都走 astream;这里断的是
    收敛那一次走的是 _generate(ainvoke 的底层)而不是 _astream。
    """
    calls: list[str] = []

    class Tracking(FakeToolChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            calls.append("generate")
            return super()._generate(messages, stop, run_manager, **kwargs)

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            calls.append("astream")
            async for chunk in super()._astream(messages, stop, run_manager, **kwargs):
                yield chunk

    patch_model(monkeypatch, Tracking(rounds=[
        tool_call_chunks("query_logistics", '{"order_id": "1001"}'),
        text_chunks("已到杭州。"),
    ]))
    await service.run_agent_turn(
        session_factory, session_id="s1", user_id="u1", message="订单 1001 的物流"
    )

    # turn1 一次 astream,收敛一次 generate
    assert calls == ["astream", "generate"]


async def test_persists_the_same_four_rows_as_the_streaming_exit(session_factory, monkeypatch):
    """两个出口的落库必须一致 —— 同一个核心,不该有第二种账本形状。"""
    patch_model(monkeypatch, FakeToolChatModel(rounds=[
        tool_call_chunks("query_logistics", '{"order_id": "1001"}'),
        text_chunks("已到杭州。"),
    ]))
    await service.run_agent_turn(
        session_factory, session_id="s1", user_id="u1", message="订单 1001 的物流"
    )

    with session_factory() as s:
        rows = s.scalars(select(Message).order_by(Message.id)).all()
    assert [r.role for r in rows] == [
        MsgRole.user, MsgRole.assistant, MsgRole.tool, MsgRole.assistant
    ]


async def test_empty_answer_raises_and_persists_nothing(session_factory, monkeypatch):
    from mewhelp.ch01.service import EmptyCompletionError

    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("  ")]))
    with pytest.raises(EmptyCompletionError):
        await service.run_agent_turn(
            session_factory, session_id="s1", user_id="u1", message="在吗"
        )

    with session_factory() as s:
        assert s.query(Message).count() == 0


async def test_resumed_is_true_on_the_second_turn(session_factory, monkeypatch):
    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("A"), text_chunks("B")]))
    await service.run_agent_turn(session_factory, session_id="s1", user_id="u1", message="第一问")
    second = await service.run_agent_turn(
        session_factory, session_id="s1", user_id="u1", message="第二问"
    )
    assert second.resumed is True


async def test_eval_transport_works_for_the_shipped_cases(session_factory, monkeypatch):
    """评估集要用的形状:`result.tool_calls` 里能直接读出工具名。

    这条是 Task 17 的前置 —— eval 不解析 SSE,就靠这个字段。
    """
    patch_model(monkeypatch, FakeToolChatModel(rounds=[
        tool_call_chunks("query_faq", '{"keyword": "退货"}'),
        text_chunks("签收后 7 天内可无理由退货。"),
    ]))
    result = await service.run_agent_turn(
        session_factory, session_id="s1", user_id="u1", message="退货政策是什么"
    )

    assert [c["name"] for c in result.tool_calls] == ["query_faq"]
    assert "7 天" in result.answer
