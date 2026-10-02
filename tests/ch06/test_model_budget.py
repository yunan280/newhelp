from importlib import import_module

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from mewhelp.ch05 import schemas
from mewhelp.ch05.limits import AgentLimits, BudgetExceeded
from mewhelp.ch05.state import WorkflowContext


def boundary():
    try:
        return import_module("mewhelp.ch06.structured").invoke_json
    except ImportError:
        pytest.fail("missing bounded structured request")


def context_for(responses, *, limits=None):
    requests = []

    class Model:
        async def ainvoke(self, messages):
            requests.append(messages)
            response = responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    def factory(**kwargs):
        assert kwargs["purpose"] == "classifier"
        assert kwargs["model_name"] == "primary"
        return Model()

    context = WorkflowContext(lambda: None, lambda: None, lambda: None, limits or AgentLimits())
    context.router_model_factory = factory
    return context, requests


def response(text, tokens=20):
    return AIMessage(
        content=text,
        usage_metadata={"input_tokens": tokens, "output_tokens": 5, "total_tokens": tokens + 5},
    )


async def invoke(context, state):
    return await boundary()(
        context,
        state,
        purpose="classifier",
        messages=[HumanMessage(content="JSON please")],
        schema=schemas.IntentOutput,
        model_name="primary",
        output_tokens=128,
    )


async def test_one_repair_keeps_prior_usage_and_all_attempts():
    context, requests = context_for(
        [response("wrong"), response('{"intent":"物流","confidence":0.9}')]
    )
    result = await invoke(
        context, {"usage": {"input_tokens": 40, "output_tokens": 10}, "calls": {"understanding": 1}}
    )
    assert result.parsed.intent == "物流" and result.error is None
    assert result.usage.total == 100
    assert result.calls == {"understanding": 1, "classifier": 2}
    assert len(result.raw_responses) == len(requests) == 2


async def test_invalid_control_has_only_one_repair():
    context, requests = context_for(
        [response("bad"), response("still bad"), response('{"intent":"物流","confidence":1}')]
    )
    result = await invoke(context, {})
    assert result.parsed is None and result.error == "invalid_json"
    assert result.calls["classifier"] == 2 and len(requests) == 2


async def test_repair_cannot_spend_final_answer_reserve():
    context, requests = context_for(
        [response("bad", tokens=6000)], limits=AgentLimits(total_model_tokens=7000)
    )
    result = await invoke(context, {})
    assert result.error == "token_budget" and result.parsed is None
    assert len(requests) == 1 and result.usage.total == 6005


async def test_exhausted_initial_budget_sends_no_request():
    context, requests = context_for([], limits=AgentLimits(total_model_tokens=100))
    with pytest.raises(BudgetExceeded):
        await invoke(context, {})
    assert requests == []


async def test_provider_failure_is_not_other_or_format_repair():
    context, requests = context_for([RuntimeError("provider unavailable")])
    with pytest.raises(RuntimeError, match="provider unavailable"):
        await invoke(context, {})
    assert len(requests) == 1
