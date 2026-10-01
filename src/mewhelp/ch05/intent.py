import re
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from .limits import TokenUsage, estimate_call_tokens, observed_usage
from .prompts import CLASSIFY_SYSTEM
from .schemas import Intent, Route, StrictDTO

ROUTES: dict[Intent, Route] = {
    "物流": "business",
    "订单": "business",
    "售后": "business",
    "商品咨询": "knowledge",
    "退款退货": "knowledge",
    "投诉": "complaint",
    "闲聊": "chitchat",
}


class ClassificationError(ValueError):
    pass


class IntentOutput(StrictDTO):
    intent: Intent


class ClassificationResult(StrictDTO):
    intent: Intent
    usage: TokenUsage
    origin: Literal["local", "llm"]
    raw: str = ""
    response_model: str | None = None


def resolve_reference(text: str) -> str:
    return text


def route_intent(intent: Intent) -> Route:
    return ROUTES[intent]


def match_chitchat(text: str) -> bool:
    return bool(
        re.fullmatch(
            r"\s*(你好|您好|嗨|哈喽|hello|hi|谢谢|感谢|多谢|你是谁|你叫什么)"
            r"[!！?？。.,，\s]*",
            text,
            re.IGNORECASE,
        )
    )


def classifier_messages(text: str) -> list:
    return [SystemMessage(content=CLASSIFY_SYSTEM), HumanMessage(content=text)]


async def classify_intent(text: str, *, model) -> ClassificationResult:
    if match_chitchat(text):
        return ClassificationResult(intent="闲聊", usage=TokenUsage(), origin="local")
    messages = classifier_messages(text)
    raw = await model.ainvoke(messages)
    try:
        result = IntentOutput.model_validate_json(raw.content)
    except (ValidationError, TypeError) as exc:
        raise ClassificationError("invalid intent classifier JSON") from exc
    usage = observed_usage(
        raw.usage_metadata,
        input_bound=estimate_call_tokens(
            [{"role": m.type, "content": m.content} for m in messages], []
        ),
        output=raw.content,
    )
    return ClassificationResult(
        intent=result.intent,
        usage=usage,
        origin="llm",
        raw=raw.content,
        response_model=raw.response_metadata.get("model_name"),
    )
