import re
from pathlib import Path
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import Field

from mewhelp.ch06.config import RouterCalibration
from mewhelp.ch06.prompts import INTENT_SYSTEM
from mewhelp.ch06.structured import invoke_json

from .limits import TokenUsage
from .schemas import Confidence, Intent, IntentOutput, QueryScope, Route, StrictDTO
from .state import WorkflowContext

ROUTES: dict[Intent, Route] = {
    "物流": "business",
    "订单": "business",
    "售后": "knowledge",
    "商品咨询": "knowledge",
    "退款退货": "knowledge",
    "投诉": "complaint",
    "闲聊": "chitchat",
    "其他": "other",
}


class ClassificationError(ValueError):
    """Invalid classifier configuration; invalid JSON is an explicit safe result."""


class ClassificationResult(StrictDTO):
    intent: Intent
    confidence: Confidence
    usage: TokenUsage
    calls: dict[str, int] = Field(default_factory=dict)
    origin: Literal["llm", "cascade"]
    escalated: bool = False
    control_error: str | None = None
    raw: str = ""
    raw_responses: list[dict] = Field(default_factory=list)
    response_model: str | None = None

    def patch(self):
        return {
            "intent": self.intent,
            "intent_confidence": self.confidence,
            "classification": self.evaluation_result(),
            "usage": self.usage.model_dump(),
            "calls": self.calls,
        }

    def evaluation_result(self):
        return {
            "actual": {"intent": self.intent, "confidence": self.confidence},
            "raw_responses": self.raw_responses,
            "usage": self.usage.model_dump(),
            "calls": self.calls,
            "origin": self.origin,
            "escalated": self.escalated,
            "control_error": self.control_error,
            "failures": [self.control_error] if self.control_error else [],
        }


def resolve_reference(text: str) -> str:
    return text


def route_intent(intent: Intent, scope: QueryScope = "general") -> Route:
    if intent in {"退款退货", "售后"} and scope == "order_specific":
        return "aftersales"
    return ROUTES[intent]


def match_chitchat(text: str) -> bool:
    """Legacy presentation helper only; classifier never bypasses the model."""
    return bool(
        re.fullmatch(
            r"\s*(你好|您好|嗨|哈喽|hello|hi|谢谢|感谢|多谢|你是谁|你叫什么)"
            r"[!！?？。.,，\s]*",
            text,
            re.IGNORECASE,
        )
    )


def classifier_messages(text: str) -> list:
    return [SystemMessage(content=INTENT_SYSTEM), HumanMessage(content=text)]


def load_router_calibration(context: WorkflowContext) -> RouterCalibration:
    from mewhelp.ch06.evaluation import model_hash, runtime_hash, verify_dataset

    settings = context.router_settings
    if settings.calibration_path is None:
        raise ClassificationError("CH06 calibration_path is required")
    try:
        calibration = RouterCalibration.model_validate_json(
            settings.calibration_path.read_text(encoding="utf-8")
        )
        dataset = verify_dataset(Path(__file__).parents[3] / "eval/ch06")
    except (OSError, ValueError) as error:
        raise ClassificationError("invalid CH06 calibration") from error
    if (
        calibration.model_hash
        != model_hash(settings, classifier_max_tokens=context.limits.classifier_max_tokens)
        or calibration.intent_hash != runtime_hash("intents", settings=settings)
        or calibration.understanding_hash != runtime_hash("understanding", settings=settings)
        or calibration.dataset_hash != dataset["dataset_hash"]
        or calibration.sample_count != dataset["files"]["calibration.jsonl"]["count"]
    ):
        raise ClassificationError("stale CH06 calibration: prompt/model/data changed")
    return calibration


async def classify_intent(
    text: str, *, context: WorkflowContext, state: dict
) -> ClassificationResult:
    calibration = load_router_calibration(context)
    settings = context.router_settings
    origin = "cascade" if settings.cascade_enabled else "llm"
    call = await invoke_json(
        context,
        state,
        purpose="classifier",
        messages=classifier_messages(text),
        schema=IntentOutput,
        model_name=settings.small_model if settings.cascade_enabled else settings.primary_model,
        output_tokens=context.limits.classifier_max_tokens,
    )
    raw = list(call.raw_responses)
    escalated = False
    if (
        call.parsed
        and settings.cascade_enabled
        and call.parsed.confidence < calibration.cascade_upgrade_threshold
    ):
        escalated = True
        call = await invoke_json(
            context,
            {**state, **call.patch()},
            purpose="classifier",
            messages=classifier_messages(text),
            schema=IntentOutput,
            model_name=settings.primary_model,
            output_tokens=context.limits.classifier_max_tokens,
        )
        raw += call.raw_responses
    parsed = call.parsed
    category = (
        parsed.intent
        if parsed and parsed.confidence >= calibration.intent_min_confidence
        else "其他"
    )
    return ClassificationResult(
        intent=category,
        confidence=parsed.confidence if parsed else 0.0,
        usage=call.usage,
        calls=call.calls,
        origin=origin,
        escalated=escalated,
        control_error="invalid_classification" if call.error == "invalid_json" else call.error,
        raw=raw[-1]["content"] if raw else "",
        raw_responses=raw,
        response_model=raw[-1]["response_model"] if raw else None,
    )
