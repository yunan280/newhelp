"""Persisted human input; transport never holds the graph open while waiting."""

import asyncio
import time
from contextlib import aclosing
from uuid import uuid4

from langgraph.types import Command, interrupt
from sqlalchemy import select

from mewhelp.ch05.events import event
from mewhelp.ch05.schemas import OrderSelection, TurnResult
from mewhelp.db.models import Conversation

from .orders import list_demo_orders


class SelectionError(ValueError):
    def __init__(self, message, status_code=409):
        super().__init__(message)
        self.status_code = status_code


def prepare_selection(state: dict) -> dict:
    selection = OrderSelection(selection_id=uuid4().hex, turn_id=state["turn_id"],
                               orders=list_demo_orders(state["user_id"]))
    return {"order_selection": selection.model_dump(mode="json"),
            "selection_status": "pending", "status": "waiting_for_order",
            "stop_reason": "waiting_for_order"}


async def offer_order_selection(state, context, emit):
    selection = state["order_selection"]
    value = interrupt({"kind": "order_selection", **selection})
    if not isinstance(value, dict) or value.get("selection_id") != selection["selection_id"]:
        raise SelectionError("订单选择已经失效")
    if value.get("cancel") is True:
        return {"selection_status": "cancelled", "status": "completed",
                "stop_reason": "cancelled", "order_selection": None,
                "selected_order_id": None, "answer": "已取消本次订单选择。",
                "started_at": time.time()}
    order_id = value.get("order_id")
    if order_id not in {o["order_id"] for o in selection["orders"]
                        if o["user_id"] == state["user_id"]}:
        raise SelectionError("请选择当前用户的候选订单")
    return {"selected_order_id": order_id, "selection_status": "received",
            "status": "completed", "stop_reason": "", "started_at": time.time()}


def active_selection(snapshot):
    state = snapshot.values
    selection = state.get("order_selection")
    if not selection or state.get("selection_status") != "pending":
        return None
    for task in snapshot.tasks:
        if task.error:
            continue
        for item in task.interrupts:
            value = item.value
            if (isinstance(value, dict) and value.get("kind") == "order_selection"
                and value.get("selection_id") == selection["selection_id"]
                and value.get("turn_id") == selection["turn_id"]):
                return selection
    return None


def _check_owner(context, session_id, user_id):
    with context.session_factory() as db:
        conv = db.scalar(select(Conversation).where(Conversation.session_id == session_id))
        if conv is None:
            return False
        if conv.user_id != user_id:
            raise SelectionError("会话属于其他用户", 403)
        return True


async def pending_selection(runtime, session_id, user_id):
    from mewhelp.ch05.service import result_from_state
    async with runtime.locks.lock(session_id):
        if not await asyncio.to_thread(_check_owner, runtime.context, session_id, user_id):
            return None
        snapshot = await runtime.graph.aget_state({"configurable": {"thread_id": session_id}})
        return result_from_state(snapshot.values) if active_selection(snapshot) else None


async def cancel_pending_locked(runtime, config):
    snapshot = await runtime.graph.aget_state(config)
    selection = active_selection(snapshot)
    if selection:
        await runtime.graph.ainvoke(
            Command(resume={"cancel": True, "selection_id": selection["selection_id"]}),
            config, context=runtime.context, durability="sync",
        )


async def _remember_result(runtime, config, request, result):
    snapshot = await runtime.graph.aget_state(config)
    receipts = dict(snapshot.values.get("selection_receipts", {}))
    receipts[request.selection_id] = {"order_id": request.order_id,
                                      "result": result.model_dump(mode="json")}
    await runtime.graph.aupdate_state(config, {"selection_receipts": receipts})


async def stream_order_resume(runtime, request):
    from mewhelp.ch05.service import result_from_state
    config = {"configurable": {"thread_id": request.session_id}, "recursion_limit": 40}
    async with runtime.locks.lock(request.session_id):
        exists = await asyncio.to_thread(_check_owner, runtime.context,
                                          request.session_id, request.resolved_user_id)
        if not exists:
            raise SelectionError("会话或订单选择不存在")
        snapshot = await runtime.graph.aget_state(config)
        state = snapshot.values
        if state.get("user_id") != request.resolved_user_id:
            raise SelectionError("会话属于其他用户", 403)
        receipt = state.get("selection_receipts", {}).get(request.selection_id)
        replay = None
        if receipt:
            if receipt["order_id"] != request.order_id:
                raise SelectionError("同一订单选择不能更换参数")
            replay = TurnResult.model_validate(receipt["result"])
        selection = state.get("order_selection")
        if replay is None:
            if not selection or selection["selection_id"] != request.selection_id:
                raise SelectionError("订单卡片已经失效")
            if request.order_id not in {o["order_id"] for o in selection["orders"]
                                        if o["user_id"] == request.resolved_user_id}:
                raise SelectionError("请选择当前用户的候选订单")
            if active_selection(snapshot):
                incoming = Command(resume={"selection_id": request.selection_id,
                                           "order_id": request.order_id})
            elif state.get("selection_status") == "received" and state.get("selected_order_id") == request.order_id:
                incoming = None
                if not snapshot.next:
                    replay = result_from_state(state)
                else:
                    await runtime.graph.aupdate_state(config, {"started_at": time.time()})
            else:
                raise SelectionError("订单选择状态或参数已改变")
        yield event("session", session_id=request.session_id,
                    conversation_id=state["conversation_id"], resumed=True)
        if replay is None:
            async with asyncio.timeout(runtime.context.limits.turn_seconds):
                async with aclosing(runtime.graph.astream(
                    incoming, config, context=runtime.context, stream_mode="custom", durability="sync",
                )) as stream:
                    async for item in stream:
                        yield item
            final = await runtime.graph.aget_state(config)
            if final.next or active_selection(final):
                raise SelectionError("订单选择后的流程尚未完成")
            result = result_from_state(final.values)
        else:
            result = replay
            if result.sources:
                yield event("sources", sources=[s.model_dump() for s in result.sources], refused=result.refused)
            if result.answer:
                yield event("token", text=result.answer)
        # Saved receipt replays must not create a checkpoint over another live interrupt.
        if receipt is None:
            await _remember_result(runtime, config, request, result)
        yield event("done", **result.model_dump(mode="json"), finish_reason=result.stop_reason)


async def resume_order(runtime, request):
    async with aclosing(stream_order_resume(runtime, request)) as stream:
        async for item in stream:
            if item["event"] == "done":
                fields = dict(item["data"])
                fields.pop("finish_reason")
                return TurnResult.model_validate(fields)
    raise RuntimeError("resume ended without a completed result")
