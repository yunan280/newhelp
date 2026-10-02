"""Router boundary validation: malformed controls cannot enter business paths."""

import pytest
from pydantic import ValidationError

from mewhelp.ch05 import schemas
from mewhelp.ch05.limits import AgentLimits
from mewhelp.ch05.state import WorkflowContext


def contract(name):
    value = getattr(schemas, name, None)
    assert value is not None, f"missing router contract {name}"
    return value


@pytest.mark.parametrize("confidence", [True, "0.9", float("nan"), float("inf"), -0.1, 1.1])
def test_intent_rejects_non_numeric_or_unbounded_confidence(confidence):
    with pytest.raises(ValidationError):
        contract("IntentOutput")(intent="退款退货", confidence=confidence)


def test_intent_has_only_two_fields_and_other_fallback():
    output = contract("IntentOutput")(intent="其他", confidence=0.9)
    assert output.model_dump() == {"intent": "其他", "confidence": 0.9}
    with pytest.raises(ValidationError):
        contract("IntentOutput")(intent="退款退货", confidence=0.9, order_id="1001")


@pytest.mark.parametrize(
    "queries", [[], ["政策"], ["a", "b", "c", "d", "e"], ["a", " "], ["a", "a"]]
)
def test_expansion_requires_two_to_four_distinct_nonblank_queries(queries):
    with pytest.raises(ValidationError):
        contract("ExpansionOutput")(queries=queries)


def test_expansion_forbids_extra_fields():
    with pytest.raises(ValidationError):
        contract("ExpansionOutput")(queries=["退货期限", "拆封限制"], intent="退款退货")


def test_turn_defaults_preserve_legacy_result_and_waiting_is_explicit():
    result = schemas.TurnResult(
        session_id="s",
        conversation_id=1,
        resumed=False,
        answer="你好",
        intent="闲聊",
        route="chitchat",
        node_trace=[],
        usage={},
        calls={},
        stop_reason="completed",
    )
    assert getattr(result, "status", None) == "completed"
    assert result.resolved_question == ""
    selection = contract("OrderSelection")(selection_id="pick", turn_id="t", orders=[])
    waiting = result.model_copy(
        update={"status": "waiting_for_order", "order_selection": selection}
    )
    assert waiting.model_dump()["order_selection"]["selection_id"] == "pick"


def test_context_keeps_four_positional_dependencies():
    context = WorkflowContext(lambda: None, lambda: None, lambda: None, AgentLimits())
    assert getattr(context, "router_settings", None) is not None
    assert context.router_settings.cascade_enabled is False


def test_cost_cascade_requires_distinct_explicit_models():
    from importlib import import_module

    try:
        settings = import_module("mewhelp.ch06.config").Ch06Settings
    except ImportError:
        pytest.fail("missing router settings")
    with pytest.raises(ValidationError):
        settings(cascade_enabled=True, _env_file=None)
    with pytest.raises(ValidationError):
        settings(cascade_enabled=True, primary_model="same", small_model="same", _env_file=None)
