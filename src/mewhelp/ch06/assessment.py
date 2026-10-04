"""The main model judges one fixed order against verified policy evidence."""

import json
import re

from langchain_core.messages import HumanMessage, SystemMessage

from mewhelp.ch05.agent import stopped
from mewhelp.ch05.limits import BudgetExceeded
from mewhelp.ch05.schemas import OrderAssessment, OrderDTO
from mewhelp.config import get_settings
from mewhelp.ch07.context import history_from_payload
from mewhelp.ch07.projection import model_messages
from mewhelp.ch07.budget import ContextBudgetError

from .prompts import ASSESSMENT_SYSTEM
from .structured import invoke_json


def assessment_messages(state):
    order = OrderDTO.model_validate(state["order"])
    background = {
        "original_question": state["question"], "question": state["resolved_question"],
        "intent": state["intent"], "order": state["order"],
        "user_facts": state.get("user_facts", {}),
        "elapsed_days_since_receipt": (order.as_of - order.received_at).days if order.received_at else None,
        "policy_sources": state["evidence"]["sources"],
    }
    if state.get('history_ctx'):
        return model_messages(history_from_payload(state['history_ctx']), system=ASSESSMENT_SYSTEM,
                              question=state['question'], background={'purpose': 'assessment', **background})
    return [SystemMessage(content=ASSESSMENT_SYSTEM), HumanMessage(json.dumps(background, ensure_ascii=False))]


async def assess_order(state, context) -> dict:
    if not state.get("order") or not state.get("gate", {}).get("passed") or not state.get("evidence", {}).get("sources"):
        raise ValueError("assessment requires an owned order and passed policy gate")
    order = OrderDTO.model_validate(state["order"])
    if order.user_id != state.get("user_id", order.user_id):
        raise ValueError("order owner mismatch")
    try:
        call = await invoke_json(context, state, purpose="assessment", schema=OrderAssessment,
            model_name=get_settings().llm_model, output_tokens=context.router_settings.assessment_max_tokens,
            messages=assessment_messages(state))
    except ContextBudgetError as error:
        return {**stopped('context_budget'), 'answer': f'上下文预算不足，请缩小问题范围。{error}'}
    except BudgetExceeded:
        return stopped("token_budget")
    assessment = call.parsed
    if assessment is not None:
        cited = set(map(int, re.findall(r"\[([1-9][0-9]*)\]", assessment.explanation)))
        known = {s["number"] for s in state["evidence"]["sources"]}
        if (not cited or not cited <= known
            or any("退款原因" in text or "退货原因" in text for text in assessment.missing_facts)
            or re.search(r"(?:请问|请说明|请提供|请选择).{0,12}(?:退款原因|退货原因)", assessment.explanation)
            or re.search(r"已(?:批准退款|退款到账)", assessment.explanation)):
            assessment = None
    if assessment is None:
        return {**call.patch(), "assessment": None, **stopped(call.error or "invalid_assessment"),
                "answer": "本次未能可靠判断订单资格，请重试或联系人工客服。",
                "assessment_control": {"raw_responses": call.raw_responses, "error": call.error or "invalid_assessment"}}
    return {**call.patch(), "assessment": assessment.model_dump(),
            "assessment_control": {"raw_responses": call.raw_responses, "error": None},
            "decision": {"reply_mode": "clarify" if assessment.verdict == "needs_clarification" else "answer",
                         "suggested_actions": [], "ticket_type": None}}
