"""Ephemeral retrieval queries; never manufacture order or policy facts."""

import json
import re
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage

from mewhelp.ch05.schemas import ExpansionOutput, OrderDTO
from mewhelp.config import get_settings

from .prompts import EXPANSION_SYSTEM
from .structured import StructuredCall, invoke_json


@dataclass
class ExpansionResult:
    expanded: bool
    queries: list[str]
    diagnostics: list[str]
    call: StructuredCall | None = None

    def patch(self):
        return self.call.patch() if self.call else {}

    def evaluation_result(self):
        return {
            "actual": {"expanded": self.expanded, "queries": self.queries},
            "raw_responses": self.call.raw_responses if self.call else [],
            "usage": self.call.usage.model_dump() if self.call else {},
            "calls": self.call.calls if self.call else {},
            "failures": self.diagnostics,
        }


async def expand_queries(question: str, order: OrderDTO | None, *, context, state):
    if state.get("scope") != "order_specific" or state.get("intent") not in {"退款退货", "售后"}:
        return ExpansionResult(False, [], [])
    if order is None:
        raise ValueError("expansion requires an owned order")
    facts = order.model_dump(mode="json")
    call = await invoke_json(
        context, state, purpose="expansion", schema=ExpansionOutput,
        model_name=get_settings().llm_model,
        output_tokens=context.router_settings.expansion_max_tokens,
        messages=[SystemMessage(content=EXPANSION_SYSTEM), HumanMessage(content=json.dumps(
            {"question": question, "intent": state["intent"], "order": facts}, ensure_ascii=False,
        ))],
    )
    if call.parsed is None:
        return ExpansionResult(True, [question], [call.error or "invalid_expansion"], call)
    allowed_numbers = set(re.findall(r"\d+(?:\.\d+)?", question + json.dumps(facts)))
    unsafe = re.compile(r"已批准|批准退款|已退款|到账|用户已选择|已拆封|已开封|质量问题已确认")
    for query in call.parsed.queries:
        if set(re.findall(r"\d+(?:\.\d+)?", query)) - allowed_numbers or any(
            fragment not in question and fragment not in json.dumps(facts, ensure_ascii=False)
            for fragment in unsafe.findall(query)
        ):
            return ExpansionResult(True, [question], ["invented_expansion_fact"], call)
    return ExpansionResult(True, call.parsed.queries, [], call)
