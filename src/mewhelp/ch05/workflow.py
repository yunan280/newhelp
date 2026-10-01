"""Fixed workflow edges surround the model-controlled read-only ReAct loop."""

import asyncio
import logging
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from mewhelp.db.models import MsgRole
from mewhelp.db.repository import TurnMessage, append_messages
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
from .evidence import EvidenceEnvelope, evaluate_gate, persist_refusal, retrieve_knowledge
from .intent import (
    classifier_messages,
    classify_intent,
    match_chitchat,
    resolve_reference,
    route_intent,
)
from .limits import TokenUsage, estimate_call_tokens, reserve_call
from .prompts import CHITCHAT_REPLY, COMPLAINT_REPLY
from .schemas import ActionOffer
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
    }


async def resolve_node(state, context, emit):
    return {"resolved_question": resolve_reference(state["question"])}


async def classify_node(state, context, emit):
    text = state["resolved_question"]
    model = None
    if not match_chitchat(text):
        messages = classifier_messages(text)
        bound = estimate_call_tokens([m.model_dump() for m in messages], [])
        reserve_call(
            0,
            bound,
            context.limits.classifier_max_tokens,
            context.limits.final_max_tokens,
            context.limits,
        )
        model = context.model_factory(context.limits.classifier_max_tokens)
    result = await asyncio.wait_for(
        classify_intent(text, model=model), context.limits.request_seconds
    )
    return {
        "intent": result.intent,
        "usage": result.usage.model_dump(),
        "calls": {"classifier": int(result.origin == "llm"), "decision": 0, "answer": 0},
    }


async def route_node(state, context, emit):
    return {"route": route_intent(state["intent"])}


async def retrieve_node(state, context, emit):
    rag = await asyncio.to_thread(context.rag_factory)
    evidence = await retrieve_knowledge(
        state["resolved_question"], rag=rag, filters=SearchFilters.model_validate(state["filters"])
    )
    return {"evidence": evidence.model_dump()}


async def gate_node(state, context, emit):
    evidence = EvidenceEnvelope.model_validate(state["evidence"])
    prompt_bytes = sum(len(str(m.content).encode("utf-8")) for m in prompt_messages(state))
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


def _write_ledger(context, state):
    rows = [TurnMessage(role=MsgRole.user, content=state["question"])]
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
    with context.session_factory() as db:
        append_messages(db, conversation_id=state["conversation_id"], rows=rows)
        db.commit()


async def log_node(state, context, emit):
    ledger_error = None
    try:
        await asyncio.to_thread(_write_ledger, context, state)
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
    return {
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
        "resolve_reference": resolve_node,
        "classify_intent": classify_node,
        "route_intent": route_node,
        "retrieve_knowledge": retrieve_node,
        "confidence_gate": gate_node,
        "fallback_reply": fallback_node,
        "complaint_reply": complaint_node,
        "chitchat_reply": chitchat_node,
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
    graph.add_edge("begin_turn", "resolve_reference")
    graph.add_edge("resolve_reference", "classify_intent")
    graph.add_edge("classify_intent", "route_intent")
    graph.add_conditional_edges(
        "route_intent",
        lambda state: state["route"],
        {
            "knowledge": "retrieve_knowledge",
            "business": "agent_decide",
            "complaint": "complaint_reply",
            "chitchat": "chitchat_reply",
        },
    )
    graph.add_edge("retrieve_knowledge", "confidence_gate")
    graph.add_conditional_edges(
        "confidence_gate",
        lambda state: state["gate"]["passed"],
        {True: "agent_decide", False: "fallback_reply"},
    )
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
    ]:
        graph.add_edge(name, "log_turn")
    graph.add_edge("log_turn", END)
    return graph.compile(checkpointer=checkpointer)
