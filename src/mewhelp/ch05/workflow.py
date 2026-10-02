"""Fixed workflow edges surround the model-controlled read-only ReAct loop."""

import asyncio
import hashlib
import logging
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from mewhelp.ch06.assessment import assess_order, assessment_messages
from mewhelp.ch06.config import PolicyCalibration
from mewhelp.ch06.expansion import expand_queries
from mewhelp.ch06.orders import UnknownOrderError, load_demo_order
from mewhelp.ch06.selection import offer_order_selection, prepare_selection
from mewhelp.ch06.understanding import understand_query
from mewhelp.db.models import MsgRole
from mewhelp.db.repository import TurnMessage, append_messages_once
from mewhelp.knowledge.answering import REFUSAL_MESSAGE
from mewhelp.knowledge.filters import SearchFilters

from .agent import (
    decide_agent,
    execute_agent_tools,
    next_agent_step,
    prompt_messages,
    stream_answer,
)
from .events import event
from .evidence import (
    EvidenceEnvelope,
    evaluate_gate,
    persist_refusal,
    retrieve_knowledge,
    retrieve_policy,
)
from .intent import classify_intent, route_intent
from .limits import BudgetExceeded, TokenUsage
from .prompts import CHITCHAT_REPLY, COMPLAINT_REPLY
from .schemas import ActionOffer, OrderDTO
from .state import WorkflowContext, WorkflowState

logger = logging.getLogger(__name__)


async def begin_turn(state, context, emit):
    return {
        "resolved_question": "",
        "intent": "",
        "route": "",
        "evidence": None,
        "gate": None,
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
    }


async def resolve_node(state, context, emit):
    try:
        result = await understand_query(state["question"], state.get("messages", []),
            state.get("trusted_entities", []), context=context, state=state)
        return result.patch()
    except BudgetExceeded:
        return {"resolved_question": state["question"], "intent": "其他", "route": "other",
                "stop_reason": "token_budget", "answer": "本次查询已达到处理限额，请缩小问题范围。"}


async def classify_node(state, context, emit):
    if state.get("stop_reason"):
        return {}
    try:
        result = await classify_intent(state["resolved_question"], context=context, state=state)
    except BudgetExceeded:
        return {"intent": "其他", "intent_confidence": 0.0, "stop_reason": "token_budget",
                "answer": "本次查询已达到处理限额，请缩小问题范围。"}
    return result.patch()


async def route_node(state, context, emit):
    return {"route": route_intent(state["intent"], state.get("scope", "general"))}


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
        await asyncio.to_thread(_write_ledger, context, {**state, **update}, phase="waiting")
    except Exception as exc:
        # A failed waiting ledger is an error, never a valid pending selection.
        raise RuntimeError("waiting message ledger did not commit") from exc
    emit(event("token", text=answer))
    update["messages"] = [HumanMessage(content=state["question"], id=state["turn_id"] + "-user"),
                          AIMessage(content=answer, id=state["turn_id"] + "-waiting")]
    return update


async def load_order_node(state, context, emit):
    order = load_demo_order(state["user_id"], state["selected_order_id"])
    trace = {"call_id": state["turn_id"] + "-order", "round": 0, "name": "load_order",
             "args": {"order_id": order.order_id}, "ok": True,
             "content": order.model_dump_json(), "error": None, "elapsed_ms": 0}
    return {"order": order.model_dump(mode="json"), "answer": "", "tool_trace": [*state["tool_trace"], trace]}


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
    return {"evidence": evidence.model_dump()}


async def assessment_node(state, context, emit):
    return await assess_order(state, context)


async def retrieve_node(state, context, emit):
    rag = await asyncio.to_thread(context.rag_factory)
    evidence = await retrieve_knowledge(
        state["resolved_question"], rag=rag, filters=SearchFilters.model_validate(state["filters"])
    )
    return {"evidence": evidence.model_dump()}


async def gate_node(state, context, emit):
    evidence = EvidenceEnvelope.model_validate(state["evidence"])
    messages = assessment_messages(state) if state["route"] == "aftersales" else prompt_messages(state)
    prompt_bytes = sum(len(str(m.content).encode("utf-8")) for m in messages)
    gate = evaluate_gate(evidence, prompt_bytes=prompt_bytes)
    result = {"gate": gate.model_dump()}
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
    return await decide_agent(state, context)


async def answer_node(state, context, emit):
    result = await stream_answer(state, context, emit)
    if "calls" not in result:
        # Final preflight may stop after tool usage grew; deliver the fixed reply.
        emit(event("token", text=result["answer"]))
    return result


def _event_key(turn_id, phase, position):
    return hashlib.sha256(f"{turn_id}:{phase}:{position}".encode()).hexdigest()


def _write_ledger(context, state, *, phase="complete"):
    rows = [TurnMessage(role=MsgRole.user, content=state["question"],
                        ch06_event_key=_event_key(state["turn_id"], "user", 0))]
    for message in state["agent_messages"]:
        if isinstance(message, AIMessage) and message.tool_calls:
            rows.append(
                TurnMessage(
                    role=MsgRole.assistant,
                    content=message.content or None,
                    tool_calls=message.tool_calls,
                )
            )
        elif isinstance(message, ToolMessage):
            rows.append(
                TurnMessage(
                    role=MsgRole.tool, content=message.content, tool_call_id=message.tool_call_id
                )
            )
    sources = state["evidence"]["sources"] if state["evidence"] and not state["refused"] else []
    rows.append(
        TurnMessage(role=MsgRole.assistant, content=state["answer"], citations=sources or None)
    )
    from dataclasses import replace
    rows = [rows[0], *[replace(row, ch06_event_key=_event_key(state["turn_id"], phase, i))
                       for i, row in enumerate(rows[1:], 1)]]
    with context.session_factory() as db:
        append_messages_once(db, conversation_id=state["conversation_id"], rows=rows)
        db.commit()


async def log_node(state, context, emit):
    ledger_error = None
    try:
        await asyncio.to_thread(_write_ledger, context, state,
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
    return {
        "trusted_entities": entities[-40:],
        "ledger_error": ledger_error,
        "offers": offers,
        "offer": offer.model_dump() if offer else None,
        "messages": [
            HumanMessage(content=state["question"], id=state["turn_id"] + "-user"),
            AIMessage(content=state["answer"], id=state["turn_id"] + "-answer"),
        ],
    }


def build_workflow(checkpointer):
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
        "execute_tools": execute_agent_tools,
        "stream_answer": answer_node,
        "bounded_reply": bounded_node,
        "log_turn": log_node,
    }

    def wrap(name, operation):
        async def node(state: WorkflowState, runtime: Runtime[WorkflowContext]):
            writer = get_stream_writer()
            writer(event("node", name=name, turn_id=state["turn_id"]))
            logger.info(
                "Ch05 node=%s session=%s turn=%s", name, state["session_id"], state["turn_id"]
            )
            update = await operation(state, runtime.context, writer)
            trace = [] if name == "begin_turn" else state.get("node_trace", [])
            return {**update, "node_trace": [*trace, name]}

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
    graph.add_edge("load_order", "expand_queries")
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
    graph.add_edge("execute_tools", "agent_decide")
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
    return graph.compile(checkpointer=checkpointer)
