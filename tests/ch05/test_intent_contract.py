import json

import httpx
import pytest
from langchain_core.messages import AIMessage


def module():
    try:
        from mewhelp.ch05 import intent
    except ImportError:
        pytest.fail("intent contract is not implemented")
    return intent


@pytest.mark.parametrize(
    "value,want",
    [
        ("物流", "business"),
        ("订单", "business"),
        ("售后", "business"),
        ("商品咨询", "knowledge"),
        ("退款退货", "knowledge"),
        ("投诉", "complaint"),
        ("闲聊", "chitchat"),
    ],
)
def test_fixed_routes(value, want):
    assert module().route_intent(value) == want


def test_mixed_refund_is_not_a_local_greeting():
    m = module()
    assert m.match_chitchat("你好，订单1001能退吗") is False
    assert m.resolve_reference(" 那它呢？ ") == " 那它呢？ "
    assert all(m.match_chitchat(s) for s in ["你好", "谢谢！", "你是谁"])


@pytest.mark.parametrize(
    "raw", ['{"intent":"execute_tools"}', "not JSON", '{"intent":"闲聊","route":"agent_decide"}']
)
async def test_invalid_classifier_output_cannot_control_graph(raw):
    m = module()

    class Model:
        async def ainvoke(self, messages):
            return AIMessage(content=raw)

    with pytest.raises(m.ClassificationError):
        await m.classify_intent("帮我处理", model=Model())


async def test_sdk_sends_provider_token_limit(monkeypatch):
    from mewhelp.ch05 import config

    assert hasattr(config, "get_ch05_model"), "chapter model factory missing"
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "probe",
                "object": "chat.completion",
                "created": 1,
                "model": "probe",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": '{"intent":"订单"}'},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        )

    from mewhelp.llm import get_chat_model

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(
            config, "get_chat_model", lambda **kw: get_chat_model(http_async_client=client, **kw)
        )
        model = config.get_ch05_model(128)
        await model.ainvoke("probe")
    assert requests[0]["max_tokens"] == 128
    assert requests[0]["thinking"] == {"type": "disabled"}
    assert "max_completion_tokens" not in requests[0]
    assert "stream_options" not in requests[0]
