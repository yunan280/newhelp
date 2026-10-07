from dataclasses import replace

import pytest
from sqlalchemy import select

from mewhelp.ch05.schemas import TurnRequest
from mewhelp.ch05.service import run_turn
from mewhelp.db.models import ToolAuditLog

from .test_ticket_graph import CONTROL, install_tools, preview, ticket_call


async def test_old_receipt_replay_does_not_overwrite_new_interrupt(workflow_runtime, tmp_path, session_factory, model_factory):
    from mewhelp.ch08.confirmation import active_ticket_preview, resume_ticket
    from mewhelp.ch08.schemas import TicketResumeRequest
    await install_tools(workflow_runtime, tmp_path, session_factory)
    first = await preview(workflow_runtime, model_factory)
    request = TicketResumeRequest(session_id=first.session_id, confirmation_id=first.ticket_preview['confirmation_id'], action='confirm')
    created = await resume_ticket(workflow_runtime, request)
    second = await preview(workflow_runtime, model_factory, session=first.session_id, call_id='ticket2')
    replay = await resume_ticket(workflow_runtime, request)
    assert replay.ticket_receipt == created.ticket_receipt
    saved = await workflow_runtime.graph.aget_state({'configurable': {'thread_id': first.session_id}})
    assert active_ticket_preview(saved)['confirmation_id'] == second.ticket_preview['confirmation_id']


async def test_receipt_survives_final_model_failure(workflow_runtime, tmp_path, session_factory, model_factory):
    from mewhelp.ch08.confirmation import resume_ticket
    from mewhelp.ch08.schemas import TicketResumeRequest
    await install_tools(workflow_runtime, tmp_path, session_factory)
    waiting = await preview(workflow_runtime, model_factory)
    model_factory.fail_answer = True
    result = await resume_ticket(workflow_runtime, TicketResumeRequest(session_id=waiting.session_id, confirmation_id=waiting.ticket_preview['confirmation_id'], action='confirm'))
    assert result.ticket_receipt['ticket_no'] in result.answer


async def test_one_explicit_request_cannot_be_reissued_by_model_after_confirmation(workflow_runtime, tmp_path, session_factory, model_factory):
    from mewhelp.ch08.confirmation import resume_ticket
    from mewhelp.ch08.schemas import TicketResumeRequest
    from mewhelp.db.models import Ticket
    from sqlalchemy import func
    await install_tools(workflow_runtime, tmp_path, session_factory)
    waiting = await preview(workflow_runtime, model_factory)
    model_factory.decisions = [ticket_call('键盘坏了，帮我建售后工单', call_id='model-repeat'), CONTROL]
    result = await resume_ticket(workflow_runtime, TicketResumeRequest(session_id=waiting.session_id, confirmation_id=waiting.ticket_preview['confirmation_id'], action='confirm'))
    assert result.status == 'completed'
    assert result.tool_trace[-1].ok is False
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 1


async def test_cross_user_and_old_preview_cannot_resume(workflow_runtime, tmp_path, session_factory, model_factory):
    from mewhelp.ch08.confirmation import resume_ticket
    from mewhelp.ch08.schemas import TicketResumeRequest
    await install_tools(workflow_runtime, tmp_path, session_factory)
    waiting = await preview(workflow_runtime, model_factory)
    request = TicketResumeRequest(session_id=waiting.session_id, confirmation_id=waiting.ticket_preview['confirmation_id'], action='confirm', user_id='other')
    with pytest.raises(ValueError):
        await resume_ticket(workflow_runtime, request)
    with pytest.raises(ValueError):
        await resume_ticket(workflow_runtime, request.model_copy(update={'user_id': 'demo-user', 'confirmation_id': 'old'}))


async def test_new_message_cancels_preview_and_audits(workflow_runtime, tmp_path, session_factory, model_factory):
    from mewhelp.ch08.confirmation import pending_ticket
    await install_tools(workflow_runtime, tmp_path, session_factory)
    waiting = await preview(workflow_runtime, model_factory)
    await run_turn(workflow_runtime, TurnRequest(message='谢谢，不建了', session_id=waiting.session_id))
    assert await pending_ticket(workflow_runtime, waiting.session_id, 'demo-user') is None
    with session_factory() as db:
        assert db.scalar(select(ToolAuditLog).where(ToolAuditLog.tool_call_id == 'ticket1')).status == '权限拒绝'


async def test_live_permission_revocation_refuses_pending_call(workflow_runtime, tmp_path, session_factory, model_factory):
    from mewhelp.ch08.confirmation import resume_ticket
    from mewhelp.ch08.schemas import TicketResumeRequest
    tools = await install_tools(workflow_runtime, tmp_path, session_factory)
    waiting = await preview(workflow_runtime, model_factory)
    tools.settings.config_path.write_text('{"servers":{},"permissions":{},"builtin_permissions":{"create_ticket":"deny"}}')
    result = await resume_ticket(workflow_runtime, TicketResumeRequest(session_id=waiting.session_id, confirmation_id=waiting.ticket_preview['confirmation_id'], action='confirm'))
    assert not result.ticket_receipt
    with session_factory() as db:
        assert db.scalar(select(ToolAuditLog).where(ToolAuditLog.tool_call_id == 'ticket1')).status == '权限拒绝'
