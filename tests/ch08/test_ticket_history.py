import pytest
from langchain_core.messages import AIMessage, ToolMessage

from mewhelp.ch05.schemas import TurnRequest
from mewhelp.ch05.service import run_turn
from mewhelp.ch08.confirmation import resume_ticket
from mewhelp.ch08.schemas import TicketResumeRequest

from .test_ticket_graph import install_tools, preview


@pytest.mark.parametrize('action', ['confirm', 'cancel', 'new_message'])
async def test_preview_narration_preserves_raw_history_but_model_tool_pairs_are_contiguous(
        workflow_runtime, tmp_path, session_factory, model_factory, action):
    await install_tools(workflow_runtime, tmp_path, session_factory)
    waiting = await preview(workflow_runtime, model_factory)
    if action != 'new_message':
        await resume_ticket(workflow_runtime, TicketResumeRequest(
            session_id=waiting.session_id, confirmation_id=waiting.ticket_preview['confirmation_id'], action=action))
    request_start = len(model_factory.requests)
    await run_turn(workflow_runtime, TurnRequest(message='鼠标也坏了', session_id=waiting.session_id))
    for purpose, messages in model_factory.requests[request_start:]:
        pending = set()
        for message in messages:
            if isinstance(message, ToolMessage):
                assert message.tool_call_id in pending, (purpose, message)
                pending.remove(message.tool_call_id)
            else:
                assert not pending, f'{purpose}: non-tool message before pending results {pending}'
                if isinstance(message, AIMessage):
                    pending.update(call['id'] for call in message.tool_calls)
        assert not pending
    saved = await workflow_runtime.graph.aget_state({'configurable': {'thread_id': waiting.session_id}})
    assert any(message.id.endswith('-waiting-ticket') for message in saved.values['messages'])
    assert any(isinstance(message, ToolMessage) and message.name == 'create_ticket'
               for message in saved.values['messages'])
