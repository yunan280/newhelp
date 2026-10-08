import json
import time

import httpx
import pytest
from langchain_core.messages import AIMessage

from mewhelp.ch05.agent import decide_agent, next_agent_step, stream_answer
from mewhelp.ch05.limits import AgentLimits, TokenUsage
from mewhelp.ch05.state import WorkflowContext


def shipping_state():
    return {
        "question": "邮费是多少",
        "resolved_question": "邮费是多少",
        "messages": [],
        "agent_messages": [],
        "pending_tool_calls": [],
        "tool_trace": [],
        "decision_count": 0,
        "tool_count": 0,
        "usage": TokenUsage().model_dump(),
        "calls": {"classifier": 0, "decision": 0, "answer": 0},
        "evidence": {
            "sources": [{"number": 1, "questions": "邮费", "answer": "普通地区满99元包邮。"}]
        },
        "actions": [],
        "stop_reason": "",
        "started_at": time.time(),
    }


async def test_invalid_control_wire_repairs_once_without_tools_then_streams_text(monkeypatch):
    from mewhelp.ch05 import config
    from mewhelp.llm import get_chat_model

    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if payload.get("stream"):
            chunks = [
                {"role": "assistant", "content": "满99元包邮[1]。"},
                {},
            ]
            frames = [
                "data: "
                + json.dumps(
                    {
                        "id": "final",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "probe",
                        "choices": [
                            {
                                "index": 0,
                                "delta": chunk,
                                "finish_reason": "stop" if i else None,
                            }
                        ],
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
                for i, chunk in enumerate(chunks)
            ]
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="".join(frames) + "data: [DONE]\n\n",
            )
        return httpx.Response(
            200,
            json={
                "id": "control",
                "object": "chat.completion",
                "created": 1,
                "model": "probe",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"reply_mode":"answer","suggested_actions":[]}'
                            if payload.get("response_format")
                            else "邮费规则如下：满99元包邮。",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 100, "completion_tokens": 15, "total_tokens": 115},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(
            config, "get_chat_model", lambda **kw: get_chat_model(http_async_client=client, **kw)
        )
        context = WorkflowContext(lambda: None, config.get_ch05_model, lambda: None, AgentLimits())
        state, events = shipping_state(), []
        state.update(await decide_agent(state, context))
        state.update(await stream_answer(state, context, events.append))
    assert len(requests) == 3
    assert "response_format" not in requests[0]
    assert {t["function"]["name"] for t in requests[0]["tools"]} == {
        "query_order",
        "query_product",
    }
    assert requests[0]["max_tokens"] == 256
    assert requests[1]["response_format"] == {"type": "json_object"}
    assert requests[1]["max_tokens"] == 256 and "tools" not in requests[1]
    assert "response_format" not in requests[2] and "tools" not in requests[2]
    assert requests[2]["stream"] is True
    assert state["calls"] == {"classifier": 0, "decision": 2, "answer": 1}
    assert state["answer"] == "满99元包邮[1]。"
    assert "".join(e["data"]["text"] for e in events) == state["answer"]


@pytest.mark.parametrize(
    "content",
    [
        "邮费规则如下：满99元包邮。",
        "",
        '{"reply_mode":',
        '{"reply_mode":"approve_refund","suggested_actions":["create_ticket"]}',
        '{"reply_mode":"answer","suggested_actions":["execute_ticket"]}',
    ],
)
async def test_invalid_control_stops_after_one_correction_without_leaking_text_or_actions(content):
    class Model:
        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages, **kwargs):
            return AIMessage(
                content=content,
                usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
            )

    state = shipping_state()
    context = WorkflowContext(lambda: None, lambda *a, **kw: Model(), lambda: None, AgentLimits())
    state.update(await decide_agent(state, context))
    assert next_agent_step(state) == "bounded_reply"
    assert state["stop_reason"] == "invalid_decision"
    assert state["calls"] == {"classifier": 0, "decision": 2, "answer": 0}
    assert state["usage"]["input_tokens"] == 200 and state["usage"]["output_tokens"] == 40
    assert state["pending_tool_calls"] == [] and state["tool_trace"] == []
    assert state["actions"] == ["handoff"]
    assert "重试" in state["answer"] and "ValidationError" not in state["answer"]
    assert "满99元包邮" not in state["answer"]


@pytest.mark.parametrize("limit", ["decision_limit", "token_budget", "deadline"])
async def test_control_correction_cannot_bypass_turn_limits(limit):
    state = shipping_state()
    requests = []

    class Model:
        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            requests.append(messages)
            if limit == "deadline":
                state["started_at"] = time.time() - 181
            tokens = 64000 if limit == "token_budget" else 100
            return AIMessage(
                content="邮费规则如下",
                usage_metadata={
                    "input_tokens": tokens,
                    "output_tokens": 20,
                    "total_tokens": tokens + 20,
                },
            )

    limits = AgentLimits(max_decisions=1) if limit == "decision_limit" else AgentLimits()
    context = WorkflowContext(lambda: None, lambda *a, **kw: Model(), lambda: None, limits)
    state.update(await decide_agent(state, context))
    assert len(requests) == 1 and state["calls"]["decision"] == 1
    assert next_agent_step(state) == "bounded_reply"
    assert state["stop_reason"] == limit


async def test_control_correction_does_not_hide_provider_failure():
    requests = []

    class Model:
        def __init__(self, repair):
            self.repair = repair

        def bind_tools(self, tools):
            return self

        async def ainvoke(self, messages):
            requests.append(self.repair)
            if self.repair:
                raise RuntimeError("provider unavailable")
            return AIMessage(content="邮费规则如下")

    context = WorkflowContext(
        lambda: None,
        lambda *a, **kw: Model(kw.get("json_mode", False)),
        lambda: None,
        AgentLimits(),
    )
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await decide_agent(shipping_state(), context)
    assert requests == [False, True]
