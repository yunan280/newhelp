"""Fixed workflow edges surround the model-controlled read-only ReAct loop."""

import asyncio
import hashlib
import json
import logging
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from mewhelp.ch06.assessment import assess_order
from mewhelp.ch06.config import PolicyCalibration
from mewhelp.ch06.expansion import expand_queries
from mewhelp.ch06.orders import UnknownOrderError, load_demo_order
from mewhelp.ch06.selection import offer_order_selection, prepare_selection
from mewhelp.ch06.understanding import understand_query
from mewhelp.ch07.budget import ContextBudgetError
from mewhelp.ch07.context import rebudget_history, tag_message
from mewhelp.ch07.store import find_ledger_ids
from mewhelp.ch07.tokens import estimate_request, estimate_text
from mewhelp.db.models import MsgRole
from mewhelp.db.repository import TurnMessage, append_messages_once
from mewhelp.knowledge.answering import REFUSAL_MESSAGE
from mewhelp.knowledge.filters import SearchFilters

from .agent import (
    decide_agent,
    next_agent_step,
    stream_answer,
)
from .events import event
from .evidence import (
    EvidenceEnvelope,
    evaluate_gate,
    limit_evidence,
    persist_refusal,
    retrieve_knowledge,
    retrieve_policy,
)
from .intent import classify_intent, route_intent
from .limits import BudgetExceeded, TokenUsage
from .prompts import CHITCHAT_REPLY, COMPLAINT_REPLY, MAIN_SYSTEM
from .schemas import ActionOffer, OrderDTO
from .state import WorkflowContext, WorkflowState

logger = logging.getLogger(__name__)


async def begin_turn(state, context, emit):
    from mewhelp.ch08.ticket_intent import ticket_request_patch
    previous_ticket_request = (None if state.get('ticket_status') in
        ('submitted', 'unknown', 'cancelled', 'denied', 'failed') else state.get('ticket_request'))
    update = {
        "messages": [tag_message(HumanMessage(state["question"], id=state["turn_id"] + "-user"),
                                  turn_id=state["turn_id"])],
        "resolved_question": "",
        "intent": "",
        "route": "",
        "evidence": None,
        "gate": None,
        'retrieved_chunks': None, 'retrieval_performed': False, 'retrieval_events': [],
        'knowledge_tool_gate_pending': False,
        'answer_message_id': None, 'feedback_status': 'none',
        'knowledge_raw_usage': None, 'knowledge_assessment': None,
        "agent_messages": [],
        "pending_tool_calls": [],
        "tool_trace": [],
        "decision_count": 0,
        "tool_count": 0,
        "usage": TokenUsage().model_dump(),
        "calls": {"classifier": 0, "decision": 0, "answer": 0},
        "answer": "",
        "actions": [],
        "decision": None,
        "refused": False,
        "offer": None,
        "low_confidence_question_id": None,
        "stop_reason": "",
        "ledger_error": None,
        "status": "completed",
        "order": None,
        "order_selection": None,
        "selection_status": None,
        "selected_order_id": None,
        "scope": "general", "intent_confidence": None, "trusted_order_id": None,
        "understanding": {}, "classification": {}, "expansion": {}, "queries": [],
        "assessment": None, "assessment_control": {}, "refund_offer": None, "user_facts": {},
        'matched_tool': None,
        'tool_catalog': context.tool_snapshot.catalog() if context.tool_snapshot is not None else [],
        'tool_catalog_hash': context.tool_snapshot.fingerprint if context.tool_snapshot is not None else '',
        'ticket_request': ticket_request_patch(state['question'], state['turn_id'] + '-user', previous_ticket_request),
        'tool_queue': [], 'tool_cursor': 0, 'tool_results': [],
        'ticket_preview': None, 'ticket_status': None,
        'ticket_prepared': None, 'ticket_resume': None, 'ticket_receipt': None,
    }
    if estimate_text(state['question'], profile=context.profile) > context.settings.max_user_input_tokens:
        update.update({'intent': '其他', 'route': 'other', 'stop_reason': 'input_limit',
                       'answer': '输入超过本轮允许的额度，请缩短后重试。'})
    return update


async def resolve_node(state, context, emit):
    if state.get('stop_reason'):
        return {}
    try:
        result = await understand_query(state["question"], state.get("messages", []),
            state.get("trusted_entities", []), context=context, state=state)
        return result.patch()
    except ContextBudgetError as error:
        return {'resolved_question': state['question'], 'intent': '其他', 'route': 'other',
                'stop_reason': 'context_budget', 'answer': f'上下文预算不足，请缩小问题范围。{error}'}
    except BudgetExceeded:
        return {"resolved_question": state["question"], "intent": "其他", "route": "other",
                "stop_reason": "token_budget", "answer": "本次查询已达到处理限额，请缩小问题范围。"}


async def classify_node(state, context, emit):
    if state.get("stop_reason"):
        return {}
    try:
        result = await classify_intent(state["resolved_question"], context=context, state=state)
    except ContextBudgetError as error:
        return {'intent': '其他', 'intent_confidence': 0.0, 'stop_reason': 'context_budget',
                'answer': f'上下文预算不足，请缩小问题范围。{error}'}
    except BudgetExceeded:
        return {"intent": "其他", "intent_confidence": 0.0, "stop_reason": "token_budget",
                "answer": "本次查询已达到处理限额，请缩小问题范围。"}
    return result.patch()


async def route_node(state, context, emit):
    if state.get('ticket_request', {}).get('explicit_request'):
        return {'route': 'business'}
    return {"route": route_intent(state["intent"], state.get("scope", "general"),
                                  matched_tool=state.get('matched_tool'), snapshot=context.tool_snapshot)}


async def other_node(state, context, emit):
    return await fixed_reply(
        state,
        context,
        emit,
        answer=state.get("answer") or "我可以协助商品、订单、物流、退货退款和售后问题。请具体说一下您希望处理什么。",
        stop_reason=state.get("stop_reason") or state.get("classification", {}).get("control_error") or "completed",
    )


async def ensure_order_node(state, context, emit):
    order_id = state.get("trusted_order_id")
    if order_id:
        try:
            load_demo_order(state["user_id"], order_id)
            return {"selected_order_id": order_id}
        except UnknownOrderError:
            pass
    return {"selected_order_id": None}


async def prepare_selection_node(state, context, emit):
    update = prepare_selection(state)
    answer = "请选择一笔订单，我会核对该订单与退款/售后政策。"
    update["answer"] = answer
    try:
        ids = await asyncio.to_thread(_write_ledger, context, {**state, **update}, phase="waiting")
    except Exception as exc:
        # A failed waiting ledger is an error, never a valid pending selection.
        raise RuntimeError("waiting message ledger did not commit") from exc
    emit(event("token", text=answer))
    update["messages"] = _committed_turn_messages({**state, **update}, ids, suffix="waiting")
    return update


async def load_order_node(state, context, emit):
    from mewhelp.tools.registry import ToolRegistry
    from mewhelp.tools.workflow import build_load_order_spec

    from .agent import registry_for_context, tool_call_context
    registry = registry_for_context(context)
    if registry.get('load_order') is None:
        registry = ToolRegistry({'load_order': build_load_order_spec()}, engine=registry.engine)
    result = await registry.run('load_order', {'order_id':state['selected_order_id']},
        context=tool_call_context(state, context, tool_call_id=state['turn_id'] + '-order'))
    if not result.ok:
        return {'stop_reason':'order_lookup_failed', 'answer':result.content}
    order = OrderDTO.model_validate_json(result.content)
    trace = {"call_id": state["turn_id"] + "-order", "round": 0, "name": "load_order",
             "args": {"order_id": order.order_id}, "ok": True,
             "content": result.content, "error": None, "elapsed_ms": result.elapsed_ms}
    call = AIMessage('', id=state['turn_id'] + '-order-call', tool_calls=[{
        'id': trace['call_id'], 'name': 'load_order', 'args': trace['args'], 'type': 'tool_call'}])
    observation = ToolMessage(trace['content'], tool_call_id=trace['call_id'], name='load_order',
                              id=state['turn_id'] + '-order-result')
    return {"order": order.model_dump(mode="json"), "answer": "", "tool_trace": [*state["tool_trace"], trace],
            'messages': [tag_message(m, turn_id=state['turn_id']) for m in (call, observation)]}


async def expand_node(state, context, emit):
    try:
        result = await expand_queries(state["resolved_question"], OrderDTO.model_validate(state["order"]),
                                     context=context, state=state)
        return {**result.patch(), "queries": result.queries, "expansion": result.evaluation_result()}
    except BudgetExceeded:
        from .agent import stopped
        return stopped("token_budget")


def load_policy_calibration(context):
    path = context.router_settings.policy_calibration_path
    if path is None:
        raise RuntimeError("CH06_POLICY_CALIBRATION_PATH must be explicitly configured")
    return PolicyCalibration.model_validate_json(Path(path).read_text(encoding="utf-8"))


async def policy_node(state, context, emit):
    rag = await asyncio.to_thread(context.rag_factory)
    evidence = await retrieve_policy(state["resolved_question"], OrderDTO.model_validate(state["order"]),
        state["queries"], rag=rag, filters=SearchFilters.model_validate(state["filters"]),
        calibration=load_policy_calibration(context))
    return retrieval_patch(state, evidence, context, kind='policy')


async def assessment_node(state, context, emit):
    return await assess_order(state, context)


async def retrieve_node(state, context, emit):
    rag = await asyncio.to_thread(context.rag_factory)
    evidence = await retrieve_knowledge(
        state["resolved_question"], rag=rag, filters=SearchFilters.model_validate(state["filters"])
    )
    return retrieval_patch(state, evidence, context, kind='knowledge')


def retrieval_patch(state, evidence, context, *, kind):
    evidence = limit_evidence(evidence, top_k=context.settings.rerank_top_k)
    return {'evidence':evidence.model_dump(), 'retrieval_performed':True,
            'retrieved_chunks':evidence.retrieved_chunks,
            'retrieval_events':[*state.get('retrieval_events', []),
                               {'kind':kind, 'query':state['resolved_question'],
                                'retrieved_chunks':evidence.retrieved_chunks}]}


async def gate_node(state, context, emit):
    from .agent import registry_for_context
    prefix = estimate_request([SystemMessage(MAIN_SYSTEM)],
        [convert_to_openai_tool(t) for t in registry_for_context(context).tools()], profile=context.profile)
    actual_evidence = estimate_text(json.dumps(state['evidence']['sources'],
        ensure_ascii=False), profile=context.profile)
    try:
        history_patch = await rebudget_history(context, state,
                                               actual_fixed={'prefix': prefix, 'evidence': actual_evidence})
        state = {**state, **history_patch}
    except ContextBudgetError as error:
        history_patch = {}
        logger.error('上下文预算不足 conversation=%s error=%s', state['conversation_id'], error)
        state = {**state, 'budget_gate_error': str(error)}
    evidence = EvidenceEnvelope.model_validate(state["evidence"])
    # RAG's byte ceiling protects evidence/reranker input. Model-window checks
    # separately account for the full history and the fixed prefix.
    prompt_bytes = len(json.dumps(state['evidence']['sources'], ensure_ascii=False).encode('utf-8'))
    formal = context.confidence_profile if state.get('route') == 'knowledge' else None
    gate = evaluate_gate(evidence.model_copy(update={'threshold':0.}) if formal else evidence,
                         prompt_bytes=prompt_bytes)
    if formal:
        from mewhelp.ch09.confidence import score_evidence
        confidence = asdict(score_evidence(evidence.scores, profile=formal))
        if gate.passed:
            gate = gate.model_copy(update={'passed':confidence['passed'],
                'reason_code':confidence['reason_code'], 'reason':confidence['reason']})
        gate = gate.model_copy(update={'evidence_confidence':confidence})
        if evidence.retrieved_chunks is not None:
            snapshot = {**evidence.retrieved_chunks, 'confidence':confidence}
            evidence = evidence.model_copy(update={'retrieved_chunks':snapshot})
            state = {**state, 'retrieved_chunks':snapshot, 'evidence':evidence.model_dump()}
    if state.get('budget_gate_error'):
        gate = gate.model_copy(update={'passed': False, 'reason_code': 'unsupported_context_size',
                                      'reason': '上下文预算不足：' + state['budget_gate_error']})
    result = {**history_patch, "gate": gate.model_dump(), 'evidence':evidence.model_dump(),
              'knowledge_tool_gate_pending': False,
              'retrieved_chunks':evidence.retrieved_chunks}
    if gate.passed:
        emit(
            event(
                "sources",
                sources=[s.model_dump() for s in evidence.sources],
                refused=False,
                low_confidence_question_id=None,
            )
        )
    else:
        result["low_confidence_question_id"] = await persist_refusal(context, state, gate)
        result["refused"] = True
    return result


async def fixed_reply(state, context, emit, *, answer, **updates):
    receipt = state.get('ticket_receipt')
    if receipt and receipt['ticket_no'] not in answer:
        answer += f"\n工单已提交，工单号 {receipt['ticket_no']}。"
    if state.get('ticket_status') == 'cancelled' and '取消' not in answer:
        answer += '\n本次工单已取消，未提交。'
    emit(event("token", text=answer))
    return {"answer": answer, "stop_reason": "completed", **updates}


async def fallback_node(state, context, emit):
    emit(
        event(
            "sources",
            sources=[],
            refused=True,
            low_confidence_question_id=state["low_confidence_question_id"],
        )
    )
    return await fixed_reply(
        state, context, emit, answer=REFUSAL_MESSAGE, stop_reason="weak_evidence"
    )


async def complaint_node(state, context, emit):
    return await fixed_reply(
        state, context, emit, answer=COMPLAINT_REPLY, actions=["handoff", "create_ticket"]
    )


async def chitchat_node(state, context, emit):
    return await fixed_reply(state, context, emit, answer=CHITCHAT_REPLY)


async def bounded_node(state, context, emit):
    emit(event("token", text=state["answer"]))
    return {}


async def decide_node(state, context, emit):
    if (state.get('route') == 'knowledge' and context.confidence_profile is not None
            and state.get('gate', {}).get('passed')):
        return {'pending_tool_calls': [], 'decision': {'reply_mode':'answer',
                'suggested_actions':[], 'ticket_type':None}}
    try:
        return await decide_agent(state, context)
    except Exception:
        if not state.get('ticket_receipt'):
            raise
        return {'stop_reason': 'ticket_created', 'pending_tool_calls': [],
                'answer': f"工单已提交，工单号 {state['ticket_receipt']['ticket_no']}。"}


async def answer_node(state, context, emit):
    try:
        result = await stream_answer(state, context, emit)
    except Exception:
        if not state.get('ticket_receipt'):
            raise
        return await fixed_reply(state, context, emit,
            answer=f"工单已提交，工单号 {state['ticket_receipt']['ticket_no']}。", stop_reason='ticket_created')
    receipt = state.get('ticket_receipt')
    if receipt and receipt['ticket_no'] not in result['answer']:
        notice = f"\n工单已提交，工单号 {receipt['ticket_no']}。"
        emit(event('token', text=notice))
        result['answer'] += notice
    if state.get('ticket_status') == 'cancelled' and '取消' not in result['answer']:
        notice = '\n本次工单已取消，未提交。'
        emit(event('token', text=notice))
        result['answer'] += notice
    if "calls" not in result:
        # Final preflight may stop after tool usage grew; deliver the fixed reply.
        emit(event("token", text=result["answer"]))
    return result


def _event_key(turn_id, phase, position):
    return hashlib.sha256(f"{turn_id}:{phase}:{position}".encode()).hexdigest()


def _write_ledger(context, state, *, phase="complete"):
    rows = [TurnMessage(role=MsgRole.user, content=state["question"],
                        ch06_event_key=_event_key(state["turn_id"], "user", 0))]
    tool_keys = {}
    for message in state.get('messages', []):
        if message.additional_kwargs.get('ch07', {}).get('turn_id') != state['turn_id']:
            continue
        if isinstance(message, AIMessage) and message.tool_calls:
            key = _event_key(state['turn_id'], 'tool_request', message.id)
            rows.append(TurnMessage(role=MsgRole.assistant, content=None,
                tool_calls=message.tool_calls, ch06_event_key=key))
        elif isinstance(message, ToolMessage):
            key = _event_key(state['turn_id'], 'tool_result', message.tool_call_id)
            rows.append(TurnMessage(role=MsgRole.tool, content=message.content,
                tool_call_id=message.tool_call_id, ch06_event_key=key))
        else:
            continue
        tool_keys[message.id] = key
    sources = state["evidence"]["sources"] if state["evidence"] and not state["refused"] else []
    rows.append(
        TurnMessage(role=MsgRole.assistant, content=state["answer"], citations=sources or None,
                    ch06_event_key=_event_key(state['turn_id'], phase, 'answer'))
    )
    with context.session_factory() as db:
        append_messages_once(db, conversation_id=state['conversation_id'], rows=rows[:-1])
        user_ids = find_ledger_ids(db, conversation_id=state['conversation_id'],
                                  event_keys=[rows[0].ch06_event_key])
        from mewhelp.ch09.contracts import MessageSnapshot
        snapshot = MessageSnapshot(
            turn_id=state['turn_id'], source_user_event_key=rows[0].ch06_event_key,
            source_user_message_id=str(user_ids[rows[0].ch06_event_key]), intent=state.get('intent',''),
            retrieval_performed=state.get('retrieval_performed', bool(state.get('evidence'))),
            retrieved_chunks=state.get('retrieved_chunks') or (state.get('evidence') or {}).get('retrieved_chunks'),
            retrieval_events=state.get('retrieval_events', []),
            pool_id=state.get('low_confidence_question_id'), trace_id=state.get('trace_id'),
            answer_status='waiting' if phase.startswith('waiting') else 'error' if state.get('status') == 'error' else 'completed')
        rows[-1] = replace(rows[-1], retrieval_snapshot=snapshot.model_dump(mode='json'))
        append_messages_once(db, conversation_id=state['conversation_id'], rows=rows[-1:])
        ids = find_ledger_ids(db, conversation_id=state['conversation_id'],
                              event_keys=[row.ch06_event_key for row in rows])
        db.commit()
        return {'user': ids[rows[0].ch06_event_key], 'answer': ids[rows[-1].ch06_event_key],
                'tools':{message_id:ids[key] for message_id,key in tool_keys.items()}}


def _committed_turn_messages(state, ids, *, suffix='answer'):
    current = [m for m in state.get('messages', [])
               if m.additional_kwargs.get('ch07', {}).get('turn_id') == state['turn_id']]
    user_id = state['turn_id'] + '-user'
    if not any(m.id == user_id for m in current):
        current.insert(0, HumanMessage(state['question'], id=user_id))
    current.append(AIMessage(state['answer'], id=state['turn_id'] + '-' + suffix))
    annotated = []
    for message in current:
        ledger_id = message.additional_kwargs.get('ch07', {}).get('ledger_id')
        if message.id == user_id:
            ledger_id = ids.get('user')
        elif message.id == state['turn_id'] + '-' + suffix:
            ledger_id = ids.get('answer')
        elif message.id in ids.get('tools', {}):
            ledger_id = ids['tools'][message.id]
        annotated.append(tag_message(message, turn_id=state['turn_id'], ledger_id=ledger_id,
            from_msg_id=ids.get('user', 0), upto_msg_id=ids.get('answer', 0), committed=bool(ids)))
    return annotated


async def log_node(state, context, emit):
    from mewhelp.ch06.refunds import create_refund_offer
    ledger_error = None
    ids = {}
    try:
        ids = await asyncio.to_thread(_write_ledger, context, state,
                                phase="cancel" if state.get("stop_reason") == "cancelled" else "complete")
    except Exception as exc:
        logger.exception("Ch05 message ledger failed session=%s", state["session_id"])
        ledger_error = type(exc).__name__
    offers = dict(state.get("offers", {}))
    offer = None
    if state["actions"]:
        offer = ActionOffer(
            offer_id=uuid4().hex,
            turn_id=state["turn_id"],
            actions=state["actions"],
            description=state["question"],
            ticket_type=(state.get("decision") or {}).get("ticket_type")
            or ("投诉" if state["intent"] == "投诉" else "售后"),
        )
        offers[offer.offer_id] = offer.model_dump()
    entities = list(state.get("trusted_entities", []))
    observed_ids = {t["args"].get("order_id") for t in state["tool_trace"]
                    if t["ok"] and t["name"] in {"query_order", "query_logistics", "load_order"}}
    for order_id in sorted(observed_ids - {None}):
        try:
            order = load_demo_order(state["user_id"], order_id)
        except UnknownOrderError:
            continue
        entities.append({**order.model_dump(mode="json"), "message_id": state["turn_id"] + "-answer"})
    refund_offer = create_refund_offer({**state, "ledger_error": ledger_error})
    refund_offers = dict(state.get("refund_offers", {}))
    if refund_offer:
        refund_offers[refund_offer.offer_id] = refund_offer.model_dump(mode="json")
    return {
        "refund_offer": refund_offer.model_dump(mode="json") if refund_offer else None,
        "refund_offers": refund_offers,
        "trusted_entities": entities,
        "ledger_error": ledger_error,
        'answer_message_id':str(ids['answer']) if ids and state.get('status','completed') == 'completed' else None,
        'feedback_status':'none',
        "offers": offers,
        "offer": offer.model_dump() if offer else None,
        "messages": _committed_turn_messages(state, ids),
    }


def build_workflow(checkpointer, *, callbacks=()):
    from mewhelp.ch08.confirmation import (
        await_ticket_node,
        execute_confirmed_ticket_node,
        prepare_ticket_node,
    )
    graph = StateGraph(WorkflowState, context_schema=WorkflowContext)
    operations = {
        "begin_turn": begin_turn,
        "understand_query": resolve_node,
        "classify_intent": classify_node,
        "route_intent": route_node,
        "retrieve_knowledge": retrieve_node,
        "confidence_gate": gate_node,
        "fallback_reply": fallback_node,
        "complaint_reply": complaint_node,
        "chitchat_reply": chitchat_node,
        "other_reply": other_node,
        "ensure_order": ensure_order_node,
        "prepare_order_selection": prepare_selection_node,
        "offer_order_selection": offer_order_selection,
        "load_order": load_order_node,
        "expand_queries": expand_node,
        "retrieve_policy": policy_node,
        "assess_order": assessment_node,
        "agent_decide": decide_node,
        "execute_tools": prepare_ticket_node,
        'await_ticket': await_ticket_node,
        'execute_confirmed_ticket': execute_confirmed_ticket_node,
        "stream_answer": answer_node,
        "bounded_reply": bounded_node,
        "log_turn": log_node,
    }

    def wrap(name, operation):
        async def node(state: WorkflowState, runtime: Runtime[WorkflowContext]):
            context_patch = {}
            if runtime.context.request_epoch and state.get('history_epoch') != runtime.context.request_epoch:
                context_patch = {'history_ctx': runtime.context.request_history,
                                 'history_epoch': runtime.context.request_epoch}
                state = {**state, **context_patch}
            writer = get_stream_writer()
            writer(event("node", name=name, turn_id=state["turn_id"]))
            logger.info(
                "Ch05 node=%s session=%s turn=%s", name, state["session_id"], state["turn_id"]
            )
            update = await operation(state, runtime.context, writer)
            from mewhelp.ch09.observability import current_request
            root = current_request()
            if root is not None and update.get('intent'):
                root.set_intent(update['intent'])
            trace = [] if name == "begin_turn" else state.get("node_trace", [])
            return {**context_patch, **update, "node_trace": [*trace, name]}

        return node

    for name, operation in operations.items():
        graph.add_node(name, wrap(name, operation))
    graph.add_edge(START, "begin_turn")
    graph.add_edge("begin_turn", "understand_query")
    graph.add_edge("understand_query", "classify_intent")
    graph.add_edge("classify_intent", "route_intent")
    graph.add_conditional_edges(
        "route_intent",
        lambda state: state["route"],
        {
            "knowledge": "retrieve_knowledge",
            "business": "agent_decide",
            "complaint": "complaint_reply",
            "chitchat": "chitchat_reply",
            "other": "other_reply",
            "aftersales": "ensure_order",
        },
    )
    graph.add_edge("retrieve_knowledge", "confidence_gate")
    graph.add_conditional_edges("ensure_order", lambda s: "load_order" if s.get("selected_order_id") else "prepare_order_selection")
    graph.add_edge("prepare_order_selection", "offer_order_selection")
    graph.add_conditional_edges("offer_order_selection", lambda s: "log_turn" if s.get("selection_status") == "cancelled" else "load_order")
    graph.add_conditional_edges('load_order', lambda s: 'bounded_reply' if s.get('stop_reason') else 'expand_queries')
    graph.add_conditional_edges("expand_queries", lambda s: "bounded_reply" if s.get("stop_reason") else "retrieve_policy")
    graph.add_edge("retrieve_policy", "confidence_gate")
    graph.add_conditional_edges(
        "confidence_gate",
        lambda state: "fallback_reply" if not state["gate"]["passed"] else "assess_order" if state["route"] == "aftersales" else "agent_decide",
    )
    graph.add_conditional_edges("assess_order", lambda s: "bounded_reply" if s.get("stop_reason") else "stream_answer")
    graph.add_conditional_edges(
        "agent_decide",
        next_agent_step,
        {
            "execute_tools": "execute_tools",
            "stream_answer": "stream_answer",
            "bounded_reply": "bounded_reply",
        },
    )
    def after_tool(state):
        if state.get('ticket_status') == 'pending':
            return 'await_ticket'
        if state.get('stop_reason'):
            return 'bounded_reply'
        if state.get('pending_tool_calls'):
            return 'execute_tools'
        return 'confidence_gate' if state.get('knowledge_tool_gate_pending') else 'agent_decide'
    graph.add_conditional_edges('execute_tools', after_tool,
        {n: n for n in ('await_ticket', 'bounded_reply', 'execute_tools', 'agent_decide', 'confidence_gate')})
    graph.add_edge('await_ticket', 'execute_confirmed_ticket')
    graph.add_conditional_edges('execute_confirmed_ticket', after_tool,
        {n: n for n in ('await_ticket', 'bounded_reply', 'execute_tools', 'agent_decide', 'confidence_gate')})
    for name in [
        "stream_answer",
        "bounded_reply",
        "fallback_reply",
        "complaint_reply",
        "chitchat_reply",
        "other_reply",
    ]:
        graph.add_edge(name, "log_turn")
    graph.add_edge("log_turn", END)
    compiled = graph.compile(checkpointer=checkpointer)
    return compiled.with_config(callbacks=list(callbacks)) if callbacks else compiled
