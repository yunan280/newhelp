import json
from importlib import import_module
from types import SimpleNamespace

import httpx
import pytest


async def test_router_actual_payload_uses_explicit_model_and_bounded_json(monkeypatch):
    try:
        config = import_module("mewhelp.ch06.config")
    except ImportError:
        pytest.fail("missing router model factory")
    from mewhelp import llm

    monkeypatch.setattr(
        llm,
        "get_settings",
        lambda: SimpleNamespace(
            llm_model="legacy-model",
            openai_base_url="https://test.invalid/v1",
            openai_api_key="test-key",
            llm_temperature=0.7,
        ),
    )
    payloads = []

    def handle(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 1,
                "model": "primary-response",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": '{"intent":"其他","confidence":0.1}',
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(
            config,
            "get_chat_model",
            lambda **kw: llm.get_chat_model(http_async_client=client, **kw),
        )
        model = config.get_router_model(
            purpose="classifier",
            model_name="explicit-primary",
            output_tokens=128,
            request_seconds=5,
        )
        await model.ainvoke("Return JSON")
        await llm.get_chat_model(http_async_client=client).ainvoke("normal conversation")
    assert payloads[0]["model"] == "explicit-primary"
    assert payloads[0]["max_tokens"] == 128
    assert payloads[0]["response_format"] == {"type": "json_object"}
    assert payloads[0]["thinking"] == {"type": "disabled"}
    assert "tools" not in payloads[0] and "max_completion_tokens" not in payloads[0]
    assert payloads[1]["model"] == "legacy-model"
    assert "response_format" not in payloads[1]
