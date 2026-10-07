"""Bounded ReAct nodes; buffered control, read-only tools, final text streaming."""

import asyncio
import json
import logging
import time

from langchain_core.messages import ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ValidationError

from mewhelp.ch07.budget import ContextBudgetError, check_window, compute_budget
from mewhelp.ch07.config import BudgetProfile, ContextSettings
from mewhelp.ch07.context import history_from_payload, tag_message
from mewhelp.ch07.observability import log_model
from mewhelp.ch07.projection import model_messages
from mewhelp.ch07.tokens import estimate_request, estimate_text
from mewhelp.ch07.types import HistoryContext
from mewhelp.config import get_settings
from mewhelp.tools.business import build_business_tools
from mewhelp.tools.contracts import ToolCallContext
from mewhelp.tools.registry import ToolRegistry, ToolSpec

from .limits import (
    BOUNDED_REPLY,
    BudgetExceeded,
    TokenUsage,
    observed_usage,
    reserve_call,
)
from .prompts import MAIN_SYSTEM
from .schemas import AgentDecision

logger = logging.getLogger(__name__)


def build_read_registry() -> ToolRegistry:
    return ToolRegistry({t.name: ToolSpec(t, preserve_raw=True) for t in build_business_tools()})


def registry_for_context(context) -> ToolRegistry:
    if context.tool_snapshot is None:
        return build_read_registry()
    return ToolRegistry(context.tool_snapshot.specs,
                        engine=context.tool_runtime.engine if context.tool_runtime else None)


def tool_call_context(state, context, *, tool_call_id=None):
    evidence = dict(state.get('ticket_request') or {})
    if state.get('ticket_status') in ('submitted', 'unknown', 'cancelled', 'denied', 'failed'):
        evidence['request_completed'] = True
    return ToolCallContext(conversation_id=state.get('conversation_id'),
        session_id=state.get('session_id'), user_id=state.get('user_id'),
        turn_id=state.get('turn_id'), tool_call_id=tool_call_id,
        intent_evidence=evidence,
        deadline_monotonic=time.monotonic() + max(0, remaining(state, context)))


def prompt_messages(state: dict, *, phase='decide', correction=None) -> list:
    evidence = state.get("evidence")
    sources = evidence["sources"] if evidence else []
    if state.get('history_ctx'):
        history = history_from_payload(state['history_ctx'])
    else:
        profile = BudgetProfile()
        history = HistoryContext(state.get('conversation_id', 0), 0, 0, '', (), (),
            tuple(state.get('messages', [])), (), {}, compute_budget(ContextSettings(), profile))
    background = {'phase': phase, 'resolved_question': state.get('resolved_question') or state['question'],
                  'evidence': sources, 'ticket_request': state.get('ticket_request', {}),
                  'ticket_status': state.get('ticket_status'),
                  'ticket_receipt': state.get('ticket_receipt')}
    if phase == 'answer':
        background['decision'] = state.get('decision')
        if state.get('route') == 'aftersales':
            background.update({'order': state.get('order'), 'assessment': state.get('assessment'),
                               'user_facts': state.get('user_facts', {}),
                               'answer_control': '按已确定的verdict回答，不改变资格，不宣称批准或到账。'})
    if correction:
        background['correction'] = correction
    return model_messages(history, system=MAIN_SYSTEM,
        question=state.get('question') or state['resolved_question'], background=background,
        current_react=state.get('agent_messages', []))


def input_bound(messages: list, tools: list[dict] | None = None, *, profile=None) -> int:
    return estimate_request(messages, tools or [], profile=profile or BudgetProfile())


def final_messages(state: dict) -> list:
    return prompt_messages(state, phase='answer')


def window_stop(error):
    logger.error('上下文预算不足 error=%s', error)
    return {**stopped('context_budget'), 'answer': '上下文预算不足，请缩小问题范围。'}


def stopped(reason: str) -> dict:
    return {
        "stop_reason": reason,
        "answer": BOUNDED_REPLY,
        "actions": ["handoff"],
        "pending_tool_calls": [],
    }


def invalid_control(update: dict, messages: list) -> dict:
    logger.warning("Ch05 invalid Agent correction; using fixed reply")
    return {
        **update,
        **stopped("invalid_decision"),
        "agent_messages": messages,
        "decision": None,
        "answer": "抱歉，这次未能完成处理，请重试或选择转人工。",
    }


def remaining(state, context) -> float:
    return context.limits.turn_seconds - (time.time() - state["started_at"])


async def decide_agent(state, context) -> dict:
    limits = context.limits
    if state.get("stop_reason"):
        return {}
    if remaining(state, context) <= 0:
        return stopped("deadline")
    if state["decision_count"] >= limits.max_decisions:
        return stopped("decision_limit")
    registry = registry_for_context(context)
    messages = prompt_messages(state)
    usage = TokenUsage.model_validate(state["usage"])
    schemas = [convert_to_openai_tool(tool) for tool in registry.tools()]
    bound = input_bound(messages, schemas, profile=context.profile)
    try:
        check_window(messages, schemas, settings=context.settings, profile=context.profile,
                     output_tokens=limits.decision_max_tokens,
                     remaining_tool_calls=max(0, limits.max_tools - state['tool_count']))
        reserve_call(
            usage.total,
            bound,
            limits.decision_max_tokens,
            input_bound(final_messages(state), profile=context.profile) + limits.final_max_tokens,
            limits,
        )
    except ContextBudgetError as error:
        return window_stop(error)
    except BudgetExceeded:
        return stopped("token_budget")
    model = context.model_factory(limits.decision_max_tokens).bind_tools(registry.tools())
    log_model(messages, schemas, state=state, purpose='decision',
              model_name=getattr(model, 'model_name', get_settings().llm_model), profile=context.profile)
    response = await asyncio.wait_for(
        model.ainvoke(messages), min(limits.request_seconds, remaining(state, context))
    )
    count = state["decision_count"] + 1
    calls = {**state["calls"], "decision": state["calls"]["decision"] + 1}
    usage = usage.plus(
        observed_usage(
            response.usage_metadata,
            input_bound=bound,
            output=json.dumps(response.model_dump(), ensure_ascii=False, default=str),
        )
    )
    update = {"decision_count": count, "calls": calls, "usage": usage.model_dump()}
    tool_calls = response.tool_calls
    if tool_calls:
        if state["tool_count"] + len(tool_calls) > limits.max_tools:
            return {**update, **stopped("tool_limit")}
        previous = {(t["name"], json.dumps(t["args"], sort_keys=True)) for t in state["tool_trace"]}
        keys = [(t["name"], json.dumps(t["args"], sort_keys=True)) for t in tool_calls]
        if len(set(keys)) != len(keys) or any(k in previous for k in keys):
            return {**update, **stopped("no_progress")}
        raw = response.model_copy(update={'id': state['turn_id'] + f'-call-{count}'})
        raw = tag_message(raw, turn_id=state['turn_id'])
        return {**update, "agent_messages": [*state.get('agent_messages', []), response], "pending_tool_calls": tool_calls,
                'tool_queue': tool_calls, 'tool_cursor': 0, 'tool_results': [],
                'messages': [raw]}
    try:
        decision = AgentDecision.model_validate_json(response.content)
    except (ValidationError, TypeError):
        logger.warning("Ch05 invalid Agent decision; attempting one control correction")
        if remaining(state, context) <= 0:
            return {**update, **stopped("deadline")}
        if count >= limits.max_decisions:
            return {**update, **stopped("decision_limit")}
        repair_messages = prompt_messages(state, phase='repair', correction='控制输出格式非法；本次只纠正JSON，不调用工具。')
        repair_bound = input_bound(repair_messages, profile=context.profile)
        try:
            check_window(repair_messages, [], settings=context.settings, profile=context.profile,
                         output_tokens=limits.decision_max_tokens, remaining_tool_calls=0)
            reserve_call(
                usage.total,
                repair_bound,
                limits.decision_max_tokens,
                input_bound(final_messages(state), profile=context.profile) + limits.final_max_tokens,
                limits,
            )
        except ContextBudgetError as error:
            return {**update, **window_stop(error)}
        except BudgetExceeded:
            return {**update, **stopped("token_budget")}
        repair_model = context.model_factory(limits.decision_max_tokens, json_mode=True)
        log_model(repair_messages, [], state=state, purpose='repair',
                  model_name=getattr(repair_model, 'model_name', get_settings().llm_model), profile=context.profile)
        repaired = await asyncio.wait_for(
            repair_model.ainvoke(repair_messages),
            min(limits.request_seconds, remaining(state, context)),
        )
        usage = usage.plus(
            observed_usage(
                repaired.usage_metadata,
                input_bound=repair_bound,
                output=json.dumps(repaired.model_dump(), ensure_ascii=False, default=str),
            )
        )
        update = {
            "decision_count": count + 1,
            "calls": {**calls, "decision": calls["decision"] + 1},
            "usage": usage.model_dump(),
        }
        if repaired.tool_calls:
            return invalid_control(update, state.get('agent_messages', []))
        try:
            decision = AgentDecision.model_validate_json(repaired.content)
        except (ValidationError, TypeError):
            return invalid_control(update, state.get('agent_messages', []))
    return {
        **update,
        "agent_messages": state.get('agent_messages', []),
        "pending_tool_calls": [],
        "decision": decision.model_dump(),
        "actions": list(dict.fromkeys(decision.suggested_actions)),
    }


async def execute_agent_tools(state, context, emit) -> dict:
    if remaining(state, context) <= 0:
        return stopped("deadline")
    calls = state["pending_tool_calls"]
    round_no = state["decision_count"]
    for call in calls:
        emit(
            {
                "event": "tool",
                "data": {
                    "phase": "start",
                    "call_id": call["id"],
                    "round": round_no,
                    "name": call["name"],
                    "args": call["args"],
                },
            }
        )
    results = await asyncio.wait_for(
        registry_for_context(context).run_all(calls, context=tool_call_context(state, context)), remaining(state, context)
    )
    messages, trace = list(state["agent_messages"]), list(state["tool_trace"])
    raw_messages = []
    for call, result in zip(calls, results, strict=True):
        item = {
            "call_id": call["id"],
            "round": round_no,
            "name": result.name,
            "args": result.args,
            "ok": result.ok,
            "content": result.content,
            "error": result.error,
            "elapsed_ms": result.elapsed_ms,
        }
        trace.append(item)
        emit({"event": "tool", "data": {"phase": "end", **item}})
        observation = ToolMessage(content=result.content, tool_call_id=call["id"], name=result.name,
                                   status='success' if result.ok else 'error',
                                   id=state['turn_id'] + '-result-' + call['id'])
        messages.append(observation)
        raw_messages.append(tag_message(observation, turn_id=state['turn_id']))
    overflow = any(estimate_text(result.content, profile=context.profile) > context.settings.tool_result_max_tokens
                   for result in results)
    return {
        "agent_messages": messages,
        "tool_trace": trace,
        "tool_count": state["tool_count"] + len(calls),
        "pending_tool_calls": [],
        'messages': raw_messages,
        **({**stopped('tool_result_limit'), 'answer': '查询结果超过本轮处理容量，请缩小范围或联系人工。'} if overflow else {}),
    }


async def stream_answer(state, context, emit) -> dict:
    if remaining(state, context) <= 0:
        return stopped("deadline")
    messages = final_messages(state)
    bound = input_bound(messages, profile=context.profile)
    usage = TokenUsage.model_validate(state["usage"])
    try:
        check_window(messages, [], settings=context.settings, profile=context.profile,
                     output_tokens=context.limits.final_max_tokens, remaining_tool_calls=0)
        reserve_call(usage.total, bound, context.limits.final_max_tokens, 0, context.limits)
    except ContextBudgetError as error:
        return window_stop(error)
    except BudgetExceeded:
        return stopped("token_budget")
    model = context.model_factory(context.limits.final_max_tokens, streaming=True)
    log_model(messages, [], state=state, purpose='answer',
              model_name=getattr(model, 'model_name', get_settings().llm_model), profile=context.profile)
    aggregate = None
    answer = ""
    async with asyncio.timeout(min(context.limits.request_seconds, remaining(state, context))):
        async for chunk in model.astream(messages):
            aggregate = chunk if aggregate is None else aggregate + chunk
            if isinstance(chunk.content, str) and chunk.content:
                answer += chunk.content
                emit({"event": "token", "data": {"text": chunk.content}})
    if not answer.strip():
        raise ValueError("answer model returned no text")
    usage = usage.plus(observed_usage(aggregate.usage_metadata, input_bound=bound, output=answer))
    truncated = aggregate.response_metadata.get("finish_reason") == "length"
    if truncated:
        notice = "\n\n回答已达到输出限额，内容可能不完整。请缩小问题范围后重试，或选择转人工。"
        answer += notice
        emit({"event": "token", "data": {"text": notice}})
    return {
        "answer": answer,
        "usage": usage.model_dump(),
        "calls": {**state["calls"], "answer": state["calls"]["answer"] + 1},
        **({"actions": ["handoff"]} if truncated else {}),
        "stop_reason": "output_limit"
        if truncated
        else "clarification"
        if state["decision"]["reply_mode"] == "clarify"
        else "completed",
    }


def next_agent_step(state) -> str:
    if state.get("stop_reason"):
        return "bounded_reply"
    return "execute_tools" if state.get("pending_tool_calls") else "stream_answer"
