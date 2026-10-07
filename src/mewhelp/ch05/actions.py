"""Confirm an existing suggestion, then reuse the original write tool once."""

import asyncio
import logging
import time

from mewhelp.db.models import Conversation
from mewhelp.db.repository import find_ticket_by_request_id
from mewhelp.tools.contracts import ToolCallContext, WriteAuthorization
from mewhelp.tools.permissions import arguments_hash
from mewhelp.tools.ticket import build_ticket_spec

from .schemas import TicketReceipt, TicketRequest

logger = logging.getLogger(__name__)


class ActionError(ValueError):
    def __init__(self, message, status_code=409):
        super().__init__(message)
        self.status_code = status_code


def _verified_receipt(context, request, conversation_id):
    with context.session_factory() as db:
        conv = db.get(Conversation, conversation_id)
        if (
            conv is None
            or conv.session_id != request.session_id
            or conv.user_id != request.resolved_user_id
        ):
            raise ActionError("会话归属不匹配", 403)
        row = find_ticket_by_request_id(db, request_id=request.offer_id)
        if row is None:
            return None
        if (
            row.conversation_id != conversation_id
            or row.description != request.description
            or row.ticket_type.value != request.ticket_type
        ):
            raise ActionError("该建议已用不同参数创建工单", 409)
        return TicketReceipt(
            ticket_no=row.ticket_no, ticket_type=row.ticket_type.value, replayed=True
        )


async def create_confirmed_ticket(runtime, request) -> TicketReceipt:
    request = TicketRequest.model_validate(request.model_dump())
    config = {"configurable": {"thread_id": request.session_id}}
    async with runtime.locks.lock(request.session_id):
        snapshot = await runtime.graph.aget_state(config)
        state = snapshot.values
        offer = state.get("offers", {}).get(request.offer_id)
        if offer is None or "create_ticket" not in offer["actions"]:
            raise ActionError("没有可确认的建工单建议", 404)
        if state.get("user_id") != request.resolved_user_id:
            raise ActionError("会话归属不匹配", 403)
        receipt = await asyncio.to_thread(
            _verified_receipt, runtime.context, request, state["conversation_id"]
        )
        if receipt is None:
            auth = legacy_button_authorization(state, request)
            args = {'description': request.description, 'ticket_type': request.ticket_type}
            tools = runtime.context.tool_runtime
            if tools is not None:
                catalog, engine = await tools.refresh(), tools.engine
            else:
                from mewhelp.tools.audit import ToolAuditWriter
                from mewhelp.tools.engine import ToolExecutionEngine
                from mewhelp.tools.registry import ToolRegistry
                engine = ToolExecutionEngine(ToolAuditWriter(runtime.context.session_factory))
                catalog = ToolRegistry({'create_ticket': build_ticket_spec(runtime.context.session_factory)}).snapshot()
            try:
                result = await engine.execute(catalog, 'create_ticket', args, ToolCallContext(
                    state['conversation_id'], request.session_id, request.resolved_user_id,
                    tool_call_id=request.offer_id, authorization=auth,
                    deadline_monotonic=time.monotonic() + runtime.context.limits.turn_seconds))
            finally:
                if tools is None:
                    engine.audit.close()
            receipt = await asyncio.to_thread(
                _verified_receipt, runtime.context, request, state["conversation_id"]
            )
            if receipt is None:
                raise ActionError(
                    f"工单尚未确认写入，请稍后重试：{result.error or 'write_failed'}", 502
                )
            receipt = receipt.model_copy(update={"replayed": False})
        else:
            # Business receipt is authoritative; never overwrite a newer interrupt.
            return receipt
        if snapshot.next:
            return receipt
        receipts = {**state.get("ticket_receipts", {}), request.offer_id: receipt.model_dump()}
        try:
            await runtime.graph.aupdate_state(
                config, {"ticket_receipts": receipts}, as_node="log_turn"
            )
        except Exception:
            logger.exception(
                "Ticket committed but checkpoint receipt failed session=%s", request.session_id
            )
            raise
        return receipt


def legacy_button_authorization(state: dict, request: TicketRequest) -> WriteAuthorization:
    request = TicketRequest.model_validate(request.model_dump())
    offer = state.get('offers', {}).get(request.offer_id)
    if not offer or 'create_ticket' not in offer['actions']:
        raise ActionError('没有可确认的建工单建议', 404)
    if state.get('session_id') != request.session_id or state.get('user_id') != request.resolved_user_id:
        raise ActionError('会话归属不匹配', 403)
    return WriteAuthorization('legacy_button', request.offer_id, state['conversation_id'],
        request.session_id, request.resolved_user_id, 'create_ticket',
        arguments_hash({'description':request.description, 'ticket_type':request.ticket_type}))
