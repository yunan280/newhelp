import copy
import json

import pytest

from mewhelp.tools.business import build_business_tools
from mewhelp.tools.registry import ToolRegistry, ToolSpec


class ScriptedCompletion:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    async def __call__(self, messages, tools, max_tokens):
        self.requests.append(copy.deepcopy((messages, tools, max_tokens)))
        return {"choices": [{"message": next(self.responses)}]}


def call(name="query_order", call_id="order-1", args=None):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(args or {"order_id": "1001"})},
            }
        ],
    }


def api():
    try:
        from mewhelp.ch05.bare import bare_loop
        from mewhelp.ch05.limits import AgentLimits
    except ImportError:
        pytest.fail("bounded bare loop is not implemented")
    return bare_loop, AgentLimits


@pytest.fixture
def read_registry():
    return ToolRegistry({t.name: ToolSpec(t) for t in build_business_tools()})


async def test_bare_feeds_order_observation_into_next_call(read_registry):
    bare_loop, limits = api()
    complete = ScriptedCompletion(
        [
            call(),
            call("query_logistics", "logistics-1"),
            {"role": "assistant", "content": "已查到最新位置"},
        ]
    )
    result = await bare_loop(
        "先查订单1001，再查物流", complete=complete, registry=read_registry, limits=limits()
    )
    assert [t["name"] for t in result.tool_trace] == ["query_order", "query_logistics"]
    assert result.model_calls == 3
    observation = complete.requests[1][0][-1]
    assert observation["tool_call_id"] == "order-1"
    assert "订单 1001" in observation["content"]
    assert result.answer == "已查到最新位置"
    assert result.usage.input_tokens > 0 and result.usage.estimated


async def test_plain_answer_finishes_in_one_call(read_registry):
    bare_loop, limits = api()
    result = await bare_loop(
        "您好",
        complete=ScriptedCompletion([{"role": "assistant", "content": "您好"}]),
        registry=read_registry,
        limits=limits(),
    )
    assert result.model_calls == 1 and result.tool_trace == []


@pytest.mark.parametrize(
    "tool_name,args,error",
    [
        ("create_ticket", {"description": "投诉"}, "unknown_tool"),
        ("query_order", {"wrong": "1001"}, "invalid_args"),
    ],
)
async def test_bad_tools_are_observations_not_writes(read_registry, tool_name, args, error):
    bare_loop, limits = api()
    complete = ScriptedCompletion(
        [call(tool_name, args=args), {"role": "assistant", "content": "请补充信息"}]
    )
    result = await bare_loop("查一下", complete=complete, registry=read_registry, limits=limits())
    assert result.tool_trace[0]["error"] == error
    assert complete.requests[1][0][-1]["role"] == "tool"


async def test_budget_rejects_before_provider_request(read_registry):
    bare_loop, limits = api()
    complete = ScriptedCompletion([])
    result = await bare_loop(
        "订单1001", complete=complete, registry=read_registry, limits=limits(total_model_tokens=10)
    )
    assert result.model_calls == 0 and result.stop_reason == "token_budget"


async def test_repeated_identical_call_stops_without_running_it_twice(read_registry):
    bare_loop, limits = api()
    complete = ScriptedCompletion([call(), call(call_id="order-2")])
    result = await bare_loop("订单1001", complete=complete, registry=read_registry, limits=limits())
    assert len(result.tool_trace) == 1 and result.stop_reason == "no_progress"


async def test_no_fifth_decision(read_registry):
    bare_loop, limits = api()
    complete = ScriptedCompletion(
        [call(call_id=f"c{i}", args={"order_id": str(i)}) for i in range(8)]
    )
    result = await bare_loop("继续查", complete=complete, registry=read_registry, limits=limits())
    assert result.model_calls == 4 and result.stop_reason == "decision_limit"


async def test_tool_limit_prevents_ninth_execution(read_registry):
    bare_loop, limits = api()
    message = call()
    message["tool_calls"] = [
        call(call_id=f"c{i}", args={"order_id": str(i)})["tool_calls"][0] for i in range(9)
    ]
    result = await bare_loop(
        "查订单", complete=ScriptedCompletion([message]), registry=read_registry, limits=limits()
    )
    assert len(result.tool_trace) <= 8 and result.stop_reason == "tool_limit"
