from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

START = datetime(2026, 10, 8, tzinfo=UTC)
END = START + timedelta(days=1)


def observation(id, trace, *, kind="SPAN", intent="knowledge", chat=True, usage=None,
                start=START, parent=None):
    return NS(id=id, trace_id=trace, type=kind, start_time=start,
              parent_observation_id=parent, name="mewhelp.agent", model="model",
              metadata={"trace_kind": "chat" if chat else "evaluation", "intent": intent},
              output={"_mewhelp_provider_usage": usage}, usage_details={"input": 9999})


async def test_all_cursor_pages_and_only_generation_usage(ch09_module):
    root1 = observation("r1", "t1")
    root2 = observation("r2", "t2", intent="order")
    root3 = observation("r3", "t3", intent="other")
    g1 = observation("g1", "t1", kind="GENERATION", intent="未分类",
                     usage={"input_tokens": 5, "output_tokens": 2})
    g2 = observation("g2", "t1", kind="GENERATION",
                     usage={"input_tokens": 3, "output_tokens": 2})
    unknown = observation("g3", "t2", kind="GENERATION")
    zero = observation("g0", "t3", kind="GENERATION",
                       usage={"input_tokens": 0, "output_tokens": 0})
    calls = []

    def get(**kwargs):
        calls.append(kwargs)
        if kwargs.get("is_root_observation"):
            data, cursor = ([root1, root2], "roots2") if not kwargs.get("cursor") else (
                [root1, root3, observation("bg", "bg", chat=False)], None)
        elif kwargs["trace_id"] == "t1":
            data, cursor = ([g1], "children2") if not kwargs.get("cursor") else ([g1, g2], None)
        else:
            data, cursor = ([unknown] if kwargs["trace_id"] == "t2" else [zero], None)
        return NS(data=data, meta=NS(cursor=cursor))

    report = await ch09_module("costs").read_token_costs(
        client=NS(api=NS(observations=NS(get_many=get))), from_time=START, to_time=END)
    assert sum(i["total_tokens"] for i in report["items"]) == 12
    assert sum(i["unknown_usage_count"] for i in report["items"]) == 1
    by_intent = {i["intent"]: i for i in report["items"]}
    assert by_intent["knowledge"]["request_count"] == 1
    assert by_intent["knowledge"]["per_request_mean"] == 12
    assert by_intent["order"]["per_request_mean"] is None
    assert by_intent["other"]["usage_coverage"] == 1
    assert all(c.get("type") == "GENERATION" for c in calls if "trace_id" in c)
    assert report["from"] == START.isoformat() and report["to"] == END.isoformat()


async def test_window_is_half_open_and_timezone_explicit(ch09_module):
    roots = [observation("included", "a"), observation("end", "b", start=END)]
    get = Mock(side_effect=lambda **kw: NS(data=roots if kw.get("is_root_observation")
                                          else [], meta=NS(cursor=None)))
    client = NS(api=NS(observations=NS(get_many=get)))
    report = await ch09_module("costs").read_token_costs(client=client, from_time=START, to_time=END)
    assert report["items"][0]["request_count"] == 1
    assert report["items"][0]["total_tokens"] == 0
    with pytest.raises(ValueError, match="时区"):
        await ch09_module("costs").read_token_costs(
            client=client, from_time=START.replace(tzinfo=None), to_time=END)


async def test_missing_provider_usage_is_unknown_and_query_failure_visible(ch09_module):
    root = observation("root", "trace")
    generation = observation("g", "trace", kind="GENERATION", usage={"input_tokens": 7})
    get = Mock(side_effect=lambda **kw: NS(data=[root] if kw.get("is_root_observation")
                                          else [generation], meta=NS(cursor=None)))
    client = NS(api=NS(observations=NS(get_many=get)))
    report = await ch09_module("costs").read_token_costs(client=client, from_time=START, to_time=END)
    item = report["items"][0]
    assert item["input_tokens"] == 7 and item["output_tokens"] == 0
    assert item["unknown_usage_count"] == 1 and item["usage_coverage"] == 0
    get.side_effect = RuntimeError("Langfuse不可读")
    with pytest.raises(RuntimeError, match="不可读"):
        await ch09_module("costs").read_token_costs(client=client, from_time=START, to_time=END)


def test_callback_preserves_actual_inclusive_provider_usage(ch09_module, span_client):
    from langchain_core.messages import AIMessage
    callback = ch09_module("costs").ProviderUsageCallback(public_key=span_client[2])
    result = callback._convert_message_to_dict(AIMessage(
        content="回答", usage_metadata={"input_tokens": 8, "output_tokens": 4,
                                       "total_tokens": 12,
                                       "input_token_details": {"cache_read": 6}}))
    assert result["_mewhelp_provider_usage"] == {"input_tokens": 8, "output_tokens": 4}


async def test_cost_api_window_and_failed_read_are_not_empty_success(ch09_db):
    import httpx
    from fastapi import FastAPI

    from mewhelp.ch09.api import router

    app = FastAPI()
    app.include_router(router)
    app.state.ch09_runtime = NS(observation_runtime=NS(client=NS(api=NS(
        observations=NS(get_many=Mock(side_effect=RuntimeError("read failed")))))))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        bad = await c.get("/api/ch09/token-costs", params={"from": "2026-10-08", "to": END.isoformat()})
        failed = await c.get("/api/ch09/token-costs", params={"from": START.isoformat(), "to": END.isoformat()})
    assert bad.status_code == 422 and failed.status_code == 503

