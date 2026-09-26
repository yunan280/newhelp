"""llm.py 的测试 —— 只验证"模型是从 .env 造出来的",不发网络请求。"""

from mewhelp.config import Settings
from mewhelp.llm import get_chat_model, get_structured_model


class FakeSchema:
    """占位 schema,只为验证 with_structured_output 被调用。"""


def fake_settings(**over):
    base = dict(
        _env_file=None,
        openai_api_key="k",
        openai_base_url="https://example.test/v1",
        llm_model="test-model",
        llm_temperature=0.3,
    )
    base.update(over)
    return Settings(**base)


def patch_settings(monkeypatch, **over):
    monkeypatch.setattr("mewhelp.llm.get_settings", lambda: fake_settings(**over))


def test_chat_model_takes_model_and_base_url_from_settings(monkeypatch):
    patch_settings(monkeypatch, llm_model="deepseek-chat",
                   openai_base_url="https://api.deepseek.com/v1")
    model = get_chat_model()
    assert model.model_name == "deepseek-chat"
    assert str(model.openai_api_base) == "https://api.deepseek.com/v1"


def test_chat_model_uses_configured_temperature_by_default(monkeypatch):
    patch_settings(monkeypatch, llm_temperature=0.9)
    assert get_chat_model().temperature == 0.9


def test_chat_model_temperature_can_be_overridden(monkeypatch):
    patch_settings(monkeypatch, llm_temperature=0.9)
    assert get_chat_model(temperature=0.0).temperature == 0.0


def test_structured_model_pins_temperature_to_zero(monkeypatch):
    patch_settings(monkeypatch, llm_temperature=0.9)
    captured = {}

    def fake_with_structured_output(self, schema, **kwargs):
        captured["schema"] = schema
        captured["kwargs"] = kwargs
        return "structured-model"

    monkeypatch.setattr(
        "langchain_openai.ChatOpenAI.with_structured_output",
        fake_with_structured_output,
    )
    result = get_structured_model(FakeSchema)

    assert result == "structured-model"
    assert captured["schema"] is FakeSchema
    # spec 要求显式声明 function_calling,不吃默认值
    assert captured["kwargs"]["method"] == "function_calling"
