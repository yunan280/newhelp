import asyncio
import json
import time

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from mewhelp.ch05.limits import AgentLimits, TokenUsage
from mewhelp.ch05.state import WorkflowContext


def module():
    try:
        from mewhelp.ch05 import agent
    except ImportError:
        pytest.fail("bounded ReAct nodes missing")
    return agent


def initial(**updates):
    return {
        "question": "先查订单1001再查物流",
        "turn_id": "unit-turn",
        "resolved_question": "先查订单1001再查物流",
        "messages": [],
        "agent_messages": [],
        "pending_tool_calls": [],
        "tool_trace": [],
        "decision_count": 0,
        "tool_count": 0,
        "usage": TokenUsage().model_dump(),
        "calls": {"classifier": 0, "decision": 0, "answer": 0},
        "evidence": None,
        "actions": [],
        "stop_reason": "",
        "started_at": time.time(),
        **updates,
    }


def tool_message(name="query_order", order_id="1001", call_id="order-1"):
    return AIMessage(
        content="私有决策文字",
        tool_calls=[
            {"name": name, "args": {"order_id": order_id}, "id": call_id, "type": "tool_call"}
        ],
    )


def decision(mode="answer", actions=None):
    return AIMessage(
        content=json.dumps(
            {"reply_mode": mode, "suggested_actions": actions or [], "ticket_type": "售后"},
            ensure_ascii=False,
        )
    )


class ScriptedModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.requests.append(list(messages))
        return next(self.responses)


class FinalModel:
    async def astream(self, messages):
        for text in ["已查到", "物流", "最新位置"]:
            yield AIMessageChunk(content=text)


def context(model, limits=None):
    from langchain_core.tools import tool

    from mewhelp.ch08.mcp_servers.mock_data import logistics_data
    from mewhelp.tools.registry import ToolSpec
    @tool
    def query_logistics(order_id: str) -> dict:
        """离线物流 Server 处理器，模拟外部传输后的结构化响应。"""
        return logistics_data(order_id)
    registry = module().build_read_registry()
    registry.register(ToolSpec(query_logistics, source='mcp', mcp_server='logistics', permission='readonly'))
    return WorkflowContext(
        lambda: None,
        lambda *a, **kw: FinalModel() if kw.get("streaming") else model,
        lambda: None,
        limits or AgentLimits(),
        tool_snapshot=registry.snapshot(),
    )


async def drive(state, ctx, events):
    m = module()
    while True:
        state.update(await m.decide_agent(state, ctx))
        next_step = m.next_agent_step(state)
        if next_step != "execute_tools":
            if next_step == "stream_answer":
                state.update(await m.stream_answer(state, ctx, events.append))
            return state
        state.update(await m.execute_agent_tools(state, ctx, events.append))


def test_agent_cannot_execute_ticket_writes():
    registry = module().build_read_registry()
    assert set(registry.names()) == {"query_order", "query_product"}
    assert registry.get("create_ticket") is None


async def test_dependent_rounds_then_stream_without_private_decisions():
    model = ScriptedModel(
        [tool_message(), tool_message("query_logistics", call_id="log-1"), decision()]
    )
    events = []
    state = await drive(initial(), context(model), events)
    assert [(t["name"], t["round"]) for t in state["tool_trace"]] == [
        ("query_order", 1),
        ("query_logistics", 2),
    ]
    assert isinstance(model.requests[1][-1], ToolMessage)
    assert all(t['ok'] for t in state['tool_trace'])
    assert model.requests[1][-1].tool_call_id == "order-1"
    assert "订单 1001" in model.requests[1][-1].content
    tokens = [e["data"]["text"] for e in events if e["event"] == "token"]
    assert tokens == ["已查到", "物流", "最新位置"]
    assert state["answer"] == "已查到物流最新位置"
    assert state["calls"] == {"classifier": 0, "decision": 3, "answer": 1}


async def test_simple_query_needs_one_tool():
    state = await drive(
        initial(), context(ScriptedModel([tool_message("query_logistics"), decision()])), []
    )
    assert len(state["tool_trace"]) == 1


@pytest.mark.parametrize(
    "actions", [[], ["handoff"], ["create_ticket"], ["handoff", "create_ticket"]]
)
async def test_clarification_and_actions_are_metadata_only(actions):
    state = await drive(
        initial(question="查我的物流"), context(ScriptedModel([decision("clarify", actions)])), []
    )
    assert state["decision"]["reply_mode"] == "clarify"
    assert state["actions"] == actions
    assert state["tool_trace"] == []


async def test_hallucinated_write_is_failed_observation():
    state = await drive(
        initial(), context(ScriptedModel([tool_message("create_ticket"), decision()])), []
    )
    assert state["tool_trace"][0]["error"] == "unknown_tool"


@pytest.mark.parametrize("kind", ["decision_limit", "token_budget", "deadline"])
async def test_limits_block_before_model(kind):
    updates = {"decision_count": 4} if kind == "decision_limit" else {}
    if kind == "deadline":
        updates["started_at"] = time.time() - 181
    limits = AgentLimits(total_model_tokens=10) if kind == "token_budget" else AgentLimits()
    state = await drive(initial(**updates), context(ScriptedModel([]), limits), [])
    assert state["stop_reason"] == kind
    assert state["calls"]["decision"] == 0


async def test_repeated_tool_cannot_run_twice():
    state = await drive(
        initial(), context(ScriptedModel([tool_message(), tool_message(call_id="repeated")])), []
    )
    assert state["stop_reason"] == "no_progress" and len(state["tool_trace"]) == 1


async def test_tool_limit_cannot_execute_ninth():
    message = AIMessage(
        content="",
        tool_calls=[
            {"name": "query_order", "args": {"order_id": str(i)}, "id": str(i), "type": "tool_call"}
            for i in range(9)
        ],
    )
    state = await drive(initial(), context(ScriptedModel([message])), [])
    assert state["stop_reason"] == "tool_limit" and state["tool_trace"] == []


async def test_cancellation_propagates_without_answer():
    class Cancelled(ScriptedModel):
        async def ainvoke(self, messages):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await drive(initial(), context(Cancelled([])), [])
