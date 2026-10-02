import json
from importlib import import_module

import pytest
from langchain_core.messages import AIMessage

from mewhelp.ch05.limits import AgentLimits
from mewhelp.ch05.state import WorkflowContext
from mewhelp.ch06.orders import load_demo_order


async def expand(raw, *, scope="order_specific", intent="退款退货"):
    calls = []

    class Model:
        async def ainvoke(self, messages):
            calls.append(messages)
            return AIMessage(content=json.dumps(raw, ensure_ascii=False))

    context = WorkflowContext(
        lambda: None, lambda: None, lambda: None, AgentLimits(),
        router_model_factory=lambda **kw: Model(),
    )
    try:
        function = import_module("mewhelp.ch06.expansion").expand_queries
    except ImportError:
        pytest.fail("missing retrieval-side expansion")
    result = await function(
        "订单1001能退吗", load_demo_order("demo-user", "1001"),
        context=context, state={"scope": scope, "intent": intent},
    )
    return result, calls


async def test_general_faq_does_not_call_expansion_model():
    result, calls = await expand({}, scope="general")
    assert not result.expanded and result.queries == [] and calls == []


async def test_valid_expansion_keeps_distinct_queries_and_usage():
    queries = ["机械键盘退货资格有哪些要求", "机械键盘退货期限与例外规定"]
    result, calls = await expand({"queries": queries})
    assert result.expanded and result.queries == queries and len(calls) == 1


@pytest.mark.parametrize("raw", [
    {"queries": ["只查一次"]},
    {"queries": ["退货条件", "退货条件"]},
    {"queries": ["退货条件", "退货期限"], "reason": "质量"},
    {"queries": ["订单1001已拆封能退款吗", "订单1001已批准退款"]},
    {"queries": ["机械键盘签收99天内退货", "机械键盘退款条件"]},
])
async def test_invalid_or_invented_expansion_falls_back_to_original(raw):
    result, _ = await expand(raw)
    assert result.queries == ["订单1001能退吗"] and result.diagnostics

