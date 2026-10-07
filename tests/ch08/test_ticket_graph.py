from dataclasses import replace

from langchain_core.messages import AIMessage
from sqlalchemy import func, select

from mewhelp.ch05.schemas import TurnRequest
from mewhelp.ch05.service import run_turn
from mewhelp.db.models import Ticket, ToolAuditLog


CONTROL = AIMessage(content='{"reply_mode":"answer","suggested_actions":[],"ticket_type":null}')


def ticket_call(description='键盘坏了', call_id='ticket1'):
    return AIMessage('', tool_calls=[{'name': 'create_ticket', 'args': {'description': description, 'ticket_type': '售后'}, 'id': call_id, 'type': 'tool_call'}])


async def install_tools(runtime, tmp_path, session_factory):
    from mewhelp.ch08.config import ToolSystemSettings
    from mewhelp.ch08.runtime import create_tool_runtime
    config = tmp_path / 'tool-config.json'
    config.write_text('{"servers":{},"permissions":{}}')
    tools = create_tool_runtime(session_factory, settings=ToolSystemSettings(config, tmp_path / 'empty-plugins'))
    runtime.context = replace(runtime.context, tool_runtime=tools)
    return tools


async def preview(runtime, model, *, session='ticket-chat', message='键盘坏了，帮我建售后工单', call_id='ticket1'):
    model.decisions = [ticket_call(call_id=call_id), CONTROL]
    return await run_turn(runtime, TurnRequest(message=message, session_id=session))


async def test_missing_description_returns_error_to_model_and_clarifies(workflow_runtime, tmp_path, session_factory, model_factory):
    await install_tools(workflow_runtime, tmp_path, session_factory)
    model_factory.decisions = [ticket_call(), AIMessage(content='{"reply_mode":"clarify","suggested_actions":[],"ticket_type":null}')]
    result = await run_turn(workflow_runtime, TurnRequest(message='帮我建个工单', session_id='missing-ticket'))
    assert result.stop_reason == 'clarification'
    assert not result.ticket_preview
    assert result.tool_trace[0].ok is False
    assert '原话' in result.tool_trace[0].content
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 0


async def test_preview_then_confirm_writes_once_with_ticket_number(workflow_runtime, tmp_path, session_factory, model_factory):
    from mewhelp.ch08.confirmation import resume_ticket
    from mewhelp.ch08.schemas import TicketResumeRequest
    await install_tools(workflow_runtime, tmp_path, session_factory)
    waiting = await preview(workflow_runtime, model_factory)
    assert waiting.status == 'waiting_for_ticket'
    assert waiting.ticket_preview['description'] == '键盘坏了'
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 0
        assert db.scalar(select(func.count()).select_from(ToolAuditLog).where(ToolAuditLog.tool_name == 'create_ticket')) == 0
    request = TicketResumeRequest(session_id=waiting.session_id, confirmation_id=waiting.ticket_preview['confirmation_id'], action='confirm')
    result = await resume_ticket(workflow_runtime, request)
    with session_factory() as db:
        rows = db.scalars(select(Ticket)).all()
        assert len(rows) == 1
        assert rows[0].ticket_no in result.answer
        assert rows[0].request_id == request.confirmation_id
        assert db.scalar(select(ToolAuditLog).where(ToolAuditLog.tool_name == 'create_ticket')).status == '成功'
    again = await resume_ticket(workflow_runtime, request)
    assert again.ticket_receipt['ticket_no'] == result.ticket_receipt['ticket_no']


async def test_cancel_has_no_ticket_and_permission_denied_audit(workflow_runtime, tmp_path, session_factory, model_factory):
    from mewhelp.ch08.confirmation import resume_ticket
    from mewhelp.ch08.schemas import TicketResumeRequest
    await install_tools(workflow_runtime, tmp_path, session_factory)
    waiting = await preview(workflow_runtime, model_factory, session='cancel-chat')
    result = await resume_ticket(workflow_runtime, TicketResumeRequest(session_id=waiting.session_id, confirmation_id=waiting.ticket_preview['confirmation_id'], action='cancel'))
    assert '取消' in result.answer
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 0
        assert db.scalar(select(ToolAuditLog).where(ToolAuditLog.tool_name == 'create_ticket')).status == '权限拒绝'
