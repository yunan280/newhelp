"""模型接入 —— 换供应商只改 .env。

所有 OpenAI 兼容的上游(GPT / Claude 网关 / DeepSeek / Ollama)共用这一条路径。
"""

from langchain_openai import ChatOpenAI

from mewhelp.config import get_settings


def get_chat_model(*, temperature: float | None = None, **kwargs) -> ChatOpenAI:
    """按 .env 构造 ChatOpenAI。

    不传 streaming=True —— 显式调用 .astream() 就会流式,少一个可能过时的参数。
    """
    s = get_settings()
    return ChatOpenAI(
        model=s.llm_model,
        base_url=s.openai_base_url,
        api_key=s.openai_api_key,
        temperature=s.llm_temperature if temperature is None else temperature,
        **kwargs,
    )


def get_structured_model(schema, *, include_raw: bool = False, **kwargs):
    """返回绑定了结构化输出 schema 的模型。

    显式指定 method="function_calling":DeepSeek 支持 function calling,
    但 json_schema 的 strict 模式它不一定支持。
    temperature 钉死 0,让抽取结果稳定。

    include_raw=True 时 `.ainvoke()` 返回 `{"raw", "parsed", "parsing_error"}` 字典
    (形状已在 langchain-core 1.6.5 上实测):模型不调工具、或工具参数过不了 schema 时
    `parsed` 是 None,`raw` 里的原始输出是唯一能说明"它到底说了什么"的东西。
    `method` 只在这里钉一次,别在调用方再写第二遍。
    """
    return get_chat_model(temperature=0.0, **kwargs).with_structured_output(
        schema, method="function_calling", include_raw=include_raw
    )
