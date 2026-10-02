"""Explicit user submission creates a durable pending application, never a payment."""

import asyncio
import hashlib

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from mewhelp.ch05.schemas import RefundOffer, RefundReceipt
from mewhelp.db.models import Conversation, RefundApplication


class RefundError(ValueError):
    def __init__(self, message, status_code=409):
        super().__init__(message)
        self.status_code = status_code


def create_refund_offer(state: dict):
    if (state.get("intent") != "退款退货" or state.get("route") != "aftersales"
        or state.get("stop_reason") != "completed" or state.get("refused")
        or state.get("ledger_error") or (state.get("assessment") or {}).get("verdict") != "eligible"
        or not state.get("order") or not (state.get("gate") or {}).get("passed")):
        return None
    offer_id = hashlib.sha256(("ch06:refund:" + state["turn_id"]).encode()).hexdigest()
    return RefundOffer(offer_id=offer_id, turn_id=state["turn_id"], order=state["order"],
                       question=state["question"], assessment=state["assessment"],
                       sources=state["evidence"]["sources"])


def _conversation(db, session_id, user_id):
    conv = db.scalar(select(Conversation).where(Conversation.session_id == session_id))
    if conv is None:
        raise RefundError("会话不存在", 404)
    if conv.user_id != user_id:
        raise RefundError("会话属于其他用户", 403)
    return conv


def read_refund_receipt(context, offer_id, session_id, user_id, *, expected=None):
    with context.session_factory() as db:
        conv = _conversation(db, session_id, user_id)
        row = db.scalar(select(RefundApplication).where(RefundApplication.offer_id == offer_id))
        if row is None:
            return None
        if row.user_id != user_id:
            raise RefundError("申请属于其他用户", 403)
        if row.conversation_id != conv.id:
            raise RefundError("申请不属于当前会话", 409)
        if expected and (row.order_id != expected.order_id or row.reason != expected.reason):
            raise RefundError("同一退款建议不能更换订单或原因", 409)
        return RefundReceipt(application_no=row.application_no, order_id=row.order_id,
                             reason=row.reason, status=row.status, replayed=True)


def _write_application(context, request, offer):
    application_no = "RF" + hashlib.sha256(request.offer_id.encode()).hexdigest()[:30]
    with context.session_factory() as db:
        conv = _conversation(db, request.session_id, request.resolved_user_id)
        db.add(RefundApplication(application_no=application_no, offer_id=request.offer_id,
            conversation_id=conv.id, user_id=request.resolved_user_id, order_id=offer.order.order_id,
            reason=request.reason, order_snapshot=offer.order.model_dump(mode="json"),
            assessment_snapshot=offer.assessment.model_dump(mode="json"),
            policy_snapshot=[s.model_dump(mode="json") for s in offer.sources], status="pending"))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            receipt = read_refund_receipt(context, request.offer_id, request.session_id,
                                          request.resolved_user_id, expected=request)
            if receipt is None:
                raise
            return receipt
    return RefundReceipt(application_no=application_no, order_id=request.order_id,
                         reason=request.reason, status="pending", replayed=False)


async def submit_refund(runtime, request):
    config = {"configurable": {"thread_id": request.session_id}}
    async with runtime.locks.lock(request.session_id):
        # A committed receipt remains readable even when the graph/checkpointer is unavailable.
        receipt = await asyncio.to_thread(read_refund_receipt, runtime.context, request.offer_id,
                                           request.session_id, request.resolved_user_id, expected=request)
        if receipt is not None:
            return receipt
        snapshot = await runtime.graph.aget_state(config)
        state = snapshot.values
        if state.get("user_id") != request.resolved_user_id:
            raise RefundError("会话属于其他用户", 403)
        raw = state.get("refund_offer")
        if snapshot.next or not raw or raw["offer_id"] != request.offer_id:
            raise RefundError("退款表单已经失效，请重新核对订单")
        offer = RefundOffer.model_validate(raw)
        verified = create_refund_offer(state)
        if (verified is None or verified != offer
            or offer.order.user_id != request.resolved_user_id
            or offer.order.order_id != request.order_id or not offer.sources):
            raise RefundError("订单或退款资格来源与服务端建议不匹配")
        receipt = await asyncio.to_thread(_write_application, runtime.context, request, offer)
        receipts = {**state.get("refund_receipts", {}), request.offer_id: receipt.model_dump(mode="json")}
        await runtime.graph.aupdate_state(config, {"refund_receipts": receipts}, as_node="log_turn")
        return receipt
