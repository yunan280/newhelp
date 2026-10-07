from dataclasses import replace

import pytest
from langchain_core.tools import tool
from pydantic import ValidationError

from mewhelp.tools.contracts import PreparedToolCall, ToolCallContext, ToolSpec
from mewhelp.tools.permissions import arguments_hash
from mewhelp.tools.registry import ToolRegistry


@tool
def create_ticket(description: str, ticket_type: str) -> str:
    """创建工单。"""
    return 'ticket'


def fixture_preview():
    from mewhelp.ch08.confirmation import make_ticket_preview
    snapshot = ToolRegistry({'create_ticket': ToolSpec(create_ticket, permission='write')}).snapshot()
    args = {'description': '键盘坏了', 'ticket_type': '售后'}
    prepared = PreparedToolCall('create_ticket', args, 'call1', arguments_hash(args), arguments_hash(snapshot.get('create_ticket').input_schema), True)
    context = ToolCallContext(1, 'session1', 'user1')
    preview = make_ticket_preview(prepared, context)
    state = {'session_id': 'session1', 'user_id': 'user1', 'conversation_id': 1, 'ticket_preview': preview, 'ticket_status': 'pending'}
    return snapshot, state


def test_confirmation_binds_owner_schema_and_arguments():
    from mewhelp.ch08.confirmation import verify_ticket_confirmation
    from mewhelp.ch08.schemas import TicketResumeRequest
    snapshot, state = fixture_preview()
    request = TicketResumeRequest(session_id='session1', user_id='user1', confirmation_id=state['ticket_preview']['confirmation_id'], action='confirm')
    auth = verify_ticket_confirmation(state, request, snapshot)
    assert auth.args_hash == state['ticket_preview']['args_hash']
    with pytest.raises(ValueError):
        verify_ticket_confirmation(state, request.model_copy(update={'user_id': 'other'}), snapshot)
    with pytest.raises(ValueError):
        verify_ticket_confirmation(state, request.model_copy(update={'confirmation_id': 'old'}), snapshot)
    denied = ToolRegistry({'create_ticket': replace(snapshot.get('create_ticket'), available=False)}).snapshot()
    with pytest.raises(ValueError):
        verify_ticket_confirmation(state, request, denied)
    state['ticket_preview']['args']['description'] = '其他描述'
    with pytest.raises(ValueError):
        verify_ticket_confirmation(state, request, snapshot)


def test_resume_does_not_accept_model_parameters_or_confirmation_flag():
    from mewhelp.ch08.schemas import TicketResumeRequest
    with pytest.raises(ValidationError):
        TicketResumeRequest(session_id='session1', user_id='user1', confirmation_id='1', action='confirm', confirmed=True)
