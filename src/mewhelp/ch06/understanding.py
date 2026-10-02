"""LLM understanding with conservative, provenance-checked reference postprocessing."""

import json
import re
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from mewhelp.ch05.limits import TokenUsage
from mewhelp.ch05.schemas import QueryScope, UnderstandingOutput
from mewhelp.ch05.state import WorkflowContext
from mewhelp.knowledge.query import protected_fragments

from .prompts import UNDERSTANDING_SYSTEM
from .structured import StructuredCall, invoke_json

_REFERENCE = re.compile(
    r"它|这(?:个|件|单|笔|款|东西|玩意)|那(?:个|件|单|款)|该(?:商品|订单)|此单|我说的是|那个订单"
)
_COLLOQUIAL = re.compile(r"啥|咋|俺|晓得|退不|修不|能.{0,8}不[？?]?$")
_ELLIPTICAL = re.compile(r"(?:未|没|没有|已|已经)(?:拆封|开封)|(?:非|不是|是)人为损坏")
_BARE_ID = re.compile(
    r"(?<![A-Za-z0-9])\d{4,}(?![A-Za-z0-9]|[-/.]\d)(?!\s*(?:元|天|年|月|日|小时|分钟|个))"
)
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_NEW_CONDITIONS = (
    "未拆封",
    "已拆封",
    "未开封",
    "已开封",
    "没有",
    "如果",
    "只有",
    "必须",
    "不超过",
    "不支持",
    "不能",
    "且",
)
_ASSERTED_QUALIFIER = re.compile(r"不|没|未|如果|只有|必须|且|超过|至少|最多|以内|以上|以下")


def explicit_order_ids(question: str) -> set[str]:
    ids = set(_BARE_ID.findall(question))
    excluded = set(re.findall(r"(?:不是|不要查|别查)\s*(?:订单(?:号)?\s*)?(\d{4,})", question))
    return ids - excluded


def _history_rows(history: list) -> list[dict]:
    rows = []
    for message in history[-20:]:
        if isinstance(message, dict):
            row = {
                "id": message.get("id"),
                "role": message.get("role", message.get("type")),
                "content": message.get("content", ""),
            }
        else:
            row = {"id": message.id, "role": message.type, "content": message.content}
        row["content"] = str(row["content"])[:2000]
        rows.append(row)
    return rows


def _trusted_entities(history: list[dict], entities: list[dict], user_id: str | None) -> list[dict]:
    ids = {row["id"] for row in history if row["id"] and row["role"] in {"assistant", "ai", "tool"}}
    return [
        entity
        for entity in entities
        if entity.get("message_id") in ids
        and entity.get("order_id")
        and (not user_id or entity.get("user_id", user_id) == user_id)
    ]


def _fallback_scope(question: str) -> QueryScope:
    if (
        explicit_order_ids(question)
        or _REFERENCE.search(question)
        or re.search(r"我(?:要|想).*退|查.*快递|快递.*没动", question)
    ):
        return "order_specific"
    return "general"


def understanding_messages(question: str, history: list[dict], entities: list[dict]):
    return [
        SystemMessage(content=UNDERSTANDING_SYSTEM),
        HumanMessage(
            content=json.dumps(
                {"question": question, "history": history, "trusted_entities": entities},
                ensure_ascii=False,
            )
        ),
    ]


@dataclass
class UnderstandingResult:
    original: str
    question: str
    scope: QueryScope
    trusted_order_id: str | None
    call: StructuredCall
    diagnostics: list[str]

    @property
    def usage(self) -> TokenUsage:
        return self.call.usage

    @property
    def calls(self) -> dict[str, int]:
        return self.call.calls

    def evaluation_result(self) -> dict:
        return {
            "actual": {
                "question": self.question,
                "scope": self.scope,
                "trusted_order_id": self.trusted_order_id,
            },
            "raw_responses": self.call.raw_responses,
            "usage": self.usage.model_dump(),
            "calls": self.calls,
            "diagnostics": self.diagnostics,
            "control_error": self.call.error,
        }

    def patch(self) -> dict:
        return {
            "resolved_question": self.question,
            "scope": self.scope,
            "trusted_order_id": self.trusted_order_id,
            "understanding": self.evaluation_result(),
            **self.call.patch(),
        }


async def understand_query(
    question: str,
    history: list,
    trusted_entities: list[dict],
    *,
    context: WorkflowContext,
    state: dict,
) -> UnderstandingResult:
    rows = _history_rows(history)
    entities = _trusted_entities(rows, trusted_entities, state.get("user_id"))
    current = explicit_order_ids(question)
    call = await invoke_json(
        context,
        state,
        purpose="understanding",
        messages=understanding_messages(question, rows, entities),
        schema=UnderstandingOutput,
        model_name=context.router_settings.primary_model,
        output_tokens=context.router_settings.understanding_max_tokens,
    )
    parsed = call.parsed
    if parsed is None:
        return UnderstandingResult(
            question,
            question,
            _fallback_scope(question),
            next(iter(current)) if len(current) == 1 else None,
            call,
            [call.error or "invalid_understanding"],
        )
    diagnostics = []
    order_id = parsed.reference_order_id
    selected = None
    if order_id in current:
        selected = order_id
    elif order_id:
        candidates = [
            entity
            for entity in entities
            if entity["order_id"] == order_id
            and entity["message_id"] == parsed.reference_message_id
        ]
        if len({entity["order_id"] for entity in entities}) == 1 and candidates:
            selected = order_id
        else:
            diagnostics.append("untrusted_or_ambiguous_reference")
    if selected is None and len(current) == 1:
        selected = next(iter(current))
    elliptical = bool(selected and _ELLIPTICAL.fullmatch(question.strip()))
    needs_rewrite = bool(_REFERENCE.search(question) or _COLLOQUIAL.search(question) or elliptical)
    canonical = parsed.question if needs_rewrite else question
    scope = parsed.scope
    if selected:
        scope = "order_specific"
    if (
        _REFERENCE.search(question)
        and not selected
        and (scope == "order_specific" or len({entity["order_id"] for entity in entities}) > 1)
    ):
        # No backend-proven object: never turn a guessed historical number into a question.
        canonical = question
    if needs_rewrite and selected:
        product = next(
            (
                entity.get("product_name", "")
                for entity in entities
                if entity["order_id"] == selected
            ),
            "",
        )
        allowed_numbers = (
            set(_NUMBER.findall(question)) | {selected} | set(_NUMBER.findall(product))
        )
        missing = [
            fragment for fragment in protected_fragments(question) if fragment not in canonical
        ]
        # A terminal colloquial “退不” is a question particle, not a negated fact.
        if re.search(r"(?:退|修)不[？?]?$", question):
            missing = [fragment for fragment in missing if fragment != "不"]
        unsafe = (
            missing
            or set(_NUMBER.findall(canonical)) - allowed_numbers
            or any(token in canonical and token not in question for token in _NEW_CONDITIONS)
            or bool(
                _ASSERTED_QUALIFIER.search(question)
                and not re.search(r"(?:退|修)不[？?]?$", question)
                and not (elliptical and question.strip() in canonical)
            )
        )
        if unsafe or selected not in canonical:
            canonical = f"订单{selected}{'的' + product if product else ''}：{question}"
            diagnostics.append("protected_information_preserved_by_prefix")
    elif needs_rewrite and not selected:
        if set(_NUMBER.findall(canonical)) - set(_NUMBER.findall(question)) or any(
            fragment not in canonical for fragment in protected_fragments(question)
        ):
            canonical = question
            diagnostics.append("unsafe_normalization_rejected")
    return UnderstandingResult(question, canonical, scope, selected, call, diagnostics)
