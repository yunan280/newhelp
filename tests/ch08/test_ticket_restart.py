from dataclasses import replace
import asyncio
import time
from typing import Literal

from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from mewhelp.ch05.runtime import open_runtime
from mewhelp.ch05.schemas import TurnRequest
from mewhelp.ch05.service import run_turn
from mewhelp.tools.contracts import ToolSpec

from .test_ticket_graph import CONTROL, install_tools, ticket_call


async def test_write_timeout_replay_only_reads_late_receipt(workflow_runtime, tmp_path, session_factory, model_factory):
    from mewhelp.ch08.confirmation import resume_ticket
    from mewhelp.ch08.schemas import TicketResumeRequest
    from mewhelp.tools.registry import ToolRegistry
    from mewhelp.db.models import ToolAuditLog, Ticket
    from sqlalchemy import select
    tools = await install_tools(workflow_runtime, tmp_path, session_factory)
    original = tools.registry.get('create_ticket')
    calls = []
    def delayed_factory(context):
        target = original.tool_factory(context)
        @tool
        def create_ticket(description: str, ticket_type: Literal['售后', '投诉', '咨询']) -> str:
            """真实工单处理器，测试制造延迟。"""
            calls.append(1)
            time.sleep(.15)
            return target.invoke({'description': description, 'ticket_type': ticket_type})
        return create_ticket
    specs = dict(tools.registry.snapshot().specs)
    specs['create_ticket'] = replace(original, tool_factory=delayed_factory, timeout_seconds=.03, retryable=True)
    tools.registry = ToolRegistry(specs, engine=tools.engine)
    model_factory.decisions = [ticket_call(), CONTROL]
    waiting = await run_turn(workflow_runtime, TurnRequest(message='键盘坏了，帮我建售后工单', session_id='late-ticket'))
    request = TicketResumeRequest(session_id=waiting.session_id, confirmation_id=waiting.ticket_preview['confirmation_id'], action='confirm')
    timed_out = await resume_ticket(workflow_runtime, request)
    assert not timed_out.ticket_receipt
    await asyncio.sleep(.2)
    replay = await resume_ticket(workflow_runtime, request)
    assert replay.ticket_receipt['ticket_no'] in replay.answer
    assert calls == [1]
    with session_factory() as db:
        assert len(db.scalars(select(Ticket)).all()) == 1
        audit = db.scalars(select(ToolAuditLog).where(ToolAuditLog.tool_name == 'create_ticket')).all()
        assert len(audit) == 1
        assert (audit[0].status, audit[0].retry_count) == ('超时', 0)


async def test_read_before_interrupt_not_replayed_after_reopening_saver(workflow_runtime, tmp_path, session_factory, model_factory, checkpoint_settings, router_settings):
    from mewhelp.ch08.confirmation import pending_ticket, resume_ticket
    from mewhelp.ch08.schemas import TicketResumeRequest
    tools = await install_tools(workflow_runtime, tmp_path, session_factory)
    read_calls = []
    @tool
    def lookup(order_id: str) -> str:
        """查询订单示例。"""
        read_calls.append(order_id)
        return '订单已签收'
    tools.registry.register(ToolSpec(lookup))
    calls = [{'name': 'lookup', 'args': {'order_id': '1001'}, 'id': 'read1', 'type': 'tool_call'}, *ticket_call().tool_calls]
    model_factory.decisions = [AIMessage('', tool_calls=calls), CONTROL]
    waiting = await run_turn(workflow_runtime, TurnRequest(message='查询1001，键盘坏了，帮我建售后工单', session_id='restart-ticket'))
    assert waiting.status == 'waiting_for_ticket'
    assert read_calls == ['1001']
    async with open_runtime(session_factory, settings=checkpoint_settings, model_factory=model_factory,
        router_settings=router_settings, router_model_factory=model_factory.router, rag_factory=lambda: object(), tool_runtime=tools) as reopened:
        pending = await pending_ticket(reopened, waiting.session_id, 'demo-user')
        assert pending.ticket_preview == waiting.ticket_preview
        await resume_ticket(reopened, TicketResumeRequest(session_id=waiting.session_id, confirmation_id=waiting.ticket_preview['confirmation_id'], action='confirm'))
    assert read_calls == ['1001']
