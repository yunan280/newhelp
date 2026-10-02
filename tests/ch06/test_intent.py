import json

import pytest
from langchain_core.messages import AIMessage

from mewhelp.ch05 import intent
from mewhelp.ch05.limits import AgentLimits
from mewhelp.ch05.state import WorkflowContext
from mewhelp.ch06.config import Ch06Settings
from mewhelp.ch06.evaluation import model_hash, runtime_hash, verify_dataset


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setenv("CH06_SMALL_MODEL", "small-test")

    def create(outputs, *, cascade=False, minimum=0.6, upgrade=0.8):
        from pathlib import Path

        path = tmp_path / "router.json"
        path.write_text(
            json.dumps(
                {
                    "model_hash": model_hash(),
                    "understanding_hash": runtime_hash("understanding"),
                    "intent_hash": runtime_hash("intents"),
                    "dataset_hash": verify_dataset(Path("eval/ch06"))["dataset_hash"],
                    "intent_min_confidence": minimum,
                    "cascade_upgrade_threshold": upgrade,
                    "sample_count": 32,
                }
            ),
            encoding="utf-8",
        )
        requests = []

        class Model:
            async def ainvoke(self, messages):
                value = outputs.pop(0)
                if isinstance(value, Exception):
                    raise value
                return AIMessage(
                    content=value,
                    usage_metadata={"input_tokens": 30, "output_tokens": 10, "total_tokens": 40},
                )

        def models(**kwargs):
            requests.append(kwargs)
            return Model()

        context = WorkflowContext(
            lambda: None,
            lambda: None,
            lambda: None,
            AgentLimits(),
            router_settings=Ch06Settings(calibration_path=path, cascade_enabled=cascade),
            router_model_factory=models,
        )
        return context, requests

    return create


def control(category, confidence):
    return json.dumps({"intent": category, "confidence": confidence}, ensure_ascii=False)


@pytest.mark.parametrize(
    "category,scope,want",
    [
        ("退款退货", "general", "knowledge"),
        ("售后", "general", "knowledge"),
        ("退款退货", "order_specific", "aftersales"),
        ("售后", "order_specific", "aftersales"),
        ("其他", "general", "other"),
        ("物流", "order_specific", "business"),
    ],
)
def test_eight_way_fixed_routes(category, scope, want):
    assert intent.route_intent(category, scope) == want


async def test_default_uses_primary_even_with_small_model_configured(factory):
    context, requests = factory([control("闲聊", 0.9)])
    result = await intent.classify_intent("你好", context=context, state={})
    assert result.intent == "闲聊" and result.confidence == 0.9
    assert [request["model_name"] for request in requests] == ["deepseek-v4-pro"]
    assert result.calls["classifier"] == 1  # No local greeting shortcut.


async def test_low_confidence_upgrades_once_and_keeps_cumulative_usage(factory):
    context, requests = factory([control("订单", 0.3), control("退款退货", 0.95)], cascade=True)
    result = await intent.classify_intent(
        "这个能退吗",
        context=context,
        state={"usage": {"input_tokens": 20, "output_tokens": 5}, "calls": {"understanding": 1}},
    )
    assert result.intent == "退款退货" and result.escalated
    assert [request["model_name"] for request in requests] == ["small-test", "deepseek-v4-pro"]
    assert result.usage.total == 105 and result.calls == {"understanding": 1, "classifier": 2}


async def test_high_confidence_small_model_does_not_upgrade(factory):
    context, requests = factory([control("物流", 0.9)], cascade=True)
    result = await intent.classify_intent("查快递", context=context, state={})
    assert result.intent == "物流" and not result.escalated and len(requests) == 1


async def test_uncertain_primary_is_other_without_repeated_escalation(factory):
    context, requests = factory([control("售后", 0.4), control("订单", 0.4)], cascade=True)
    result = await intent.classify_intent("奇怪的问法", context=context, state={})
    assert result.intent == "其他" and result.confidence == 0.4 and len(requests) == 2


async def test_invalid_classification_stops_after_one_format_repair(factory):
    context, requests = factory(
        ['{"intent":"退款退货","confidence":1,"order_id":"1001"}', "not JSON"]
    )
    result = await intent.classify_intent("处理一下", context=context, state={})
    assert result.intent == "其他" and result.control_error == "invalid_classification"
    assert len(requests) == 2 and result.calls["classifier"] == 2


async def test_provider_failure_is_not_business_label_or_other(factory):
    context, requests = factory([RuntimeError("upstream unavailable")])
    with pytest.raises(RuntimeError, match="upstream unavailable"):
        await intent.classify_intent("想退款", context=context, state={})
    assert len(requests) == 1


async def test_missing_calibration_cannot_fall_back_to_default_small_model():
    context = WorkflowContext(
        lambda: None,
        lambda: None,
        lambda: None,
        AgentLimits(),
        router_settings=Ch06Settings(calibration_path=None),
    )
    with pytest.raises(ValueError, match="calibration"):
        await intent.classify_intent("你好", context=context, state={})


async def test_stale_calibration_does_not_send_provider_request(factory):
    context, requests = factory([])
    path = context.router_settings.calibration_path
    payload = json.loads(path.read_text())
    payload["intent_hash"] = "stale"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="calibration"):
        await intent.classify_intent("退货", context=context, state={})
    assert requests == []


@pytest.mark.parametrize("changed", ["understanding_limit", "classifier_limit"])
async def test_effective_model_request_configuration_invalidates_calibration(factory, changed):
    context, requests = factory([])
    if changed == "understanding_limit":
        context.router_settings = context.router_settings.model_copy(
            update={"understanding_max_tokens": 1024}
        )
    else:
        context.limits = AgentLimits(classifier_max_tokens=256)
    with pytest.raises(ValueError, match="calibration"):
        await intent.classify_intent("退货", context=context, state={})
    assert requests == []
