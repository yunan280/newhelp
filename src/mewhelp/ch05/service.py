"""One graph run powers JSON and SSE; checkpoint completed history owns the session."""

import asyncio
import logging
import time
from contextlib import aclosing
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage

from mewhelp.db.models import MsgRole
from mewhelp.db.repository import get_or_create_conversation, load_replay_messages

from .events import event
from .schemas import TurnResult

logger = logging.getLogger(__name__)


def _identity(context, request, session_id, *, bootstrap):
    with context.session_factory() as db:
        conv, _ = get_or_create_conversation(
            db, session_id=session_id, user_id=request.resolved_user_id
        )
        if conv.user_id != request.resolved_user_id:
            raise ValueError("session belongs to another user")
        messages = []
        if bootstrap:
            for row in load_replay_messages(db, conversation_id=conv.id):
                cls = HumanMessage if row.role is MsgRole.user else AIMessage
                messages.append(cls(content=row.content or "", id=f"mysql-{row.id}"))
        db.commit()
        return conv.id, messages


def result_from_state(state: dict) -> TurnResult:
    fields = {name: state[name] for name in TurnResult.model_fields if name in state}
    fields["sources"] = (
        state["evidence"]["sources"] if state["evidence"] and not state["refused"] else []
    )
    return TurnResult.model_validate(fields)


async def stream_turn(runtime, request, *, entry_point):
    session_id = request.session_id or uuid4().hex
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 40}
    async with runtime.locks.lock(session_id):
        previous = await runtime.graph.aget_state(config)
        cid, history = await asyncio.to_thread(
            _identity, runtime.context, request, session_id, bootstrap=not previous.values
        )
        resumed = bool(previous.values.get("messages") or history)
        incoming = {
            "question": request.message,
            "session_id": session_id,
            "user_id": request.resolved_user_id,
            "conversation_id": cid,
            "resumed": resumed,
            "turn_id": uuid4().hex,
            "filters": request.filters.model_dump() if request.filters else {},
            "entry_point": entry_point,
            "started_at": time.time(),
        }
        if history:
            incoming["messages"] = history
        yield event("session", session_id=session_id, conversation_id=cid, resumed=resumed)
        try:
            async with asyncio.timeout(runtime.context.limits.turn_seconds):
                async with aclosing(
                    runtime.graph.astream(
                        incoming,
                        config,
                        context=runtime.context,
                        stream_mode="custom",
                        durability="sync",
                    )
                ) as stream:
                    async for item in stream:
                        yield item
            snapshot = await runtime.graph.aget_state(config)
            result = result_from_state(snapshot.values)
            if result.offer:
                yield event("actions", actions=result.actions, offer=result.offer.model_dump())
            yield event("done", **result.model_dump(), finish_reason=result.stop_reason)
        except Exception:
            logger.exception("Ch05 turn failed session=%s", session_id)
            raise


async def run_turn(runtime, request) -> TurnResult:
    async with aclosing(stream_turn(runtime, request, entry_point="agent")) as stream:
        async for item in stream:
            if item["event"] == "done":
                fields = dict(item["data"])
                fields.pop("finish_reason")
                return TurnResult.model_validate(fields)
    raise RuntimeError("workflow ended without a completed result")
