from dataclasses import replace

import pytest
from sqlalchemy import func, select

from mewhelp.ch05.schemas import TurnRequest
from mewhelp.ch05.service import run_turn
from mewhelp.db.models import Ticket
from tests.ch08.test_ticket_graph import CONTROL, install_tools, ticket_call


class AutoOmittingModel:
    """Reproduce the provider choosing answer despite a complete ticket request."""

    def __init__(self, choices, *, ignores_choice=False):
        self.choices = choices
        self.ignores_choice = ignores_choice
        self.choice = None

    def bind_tools(self, tools, **kwargs):
        self.choice = kwargs.get('tool_choice')
        self.choices.append(self.choice)
        return self

    async def ainvoke(self, messages):
        if self.choice == 'create_ticket' and not self.ignores_choice:
            return ticket_call('耳机左侧没有声音', 'required-ticket')
        return CONTROL


async def configure(runtime, model_factory, tmp_path, session_factory, *, ignores_choice=False):
    await install_tools(runtime, tmp_path, session_factory)
    choices = []

    def factory(tokens, **kwargs):
        if tokens == runtime.context.limits.decision_max_tokens and not kwargs.get('streaming') and not kwargs.get('json_mode'):
            return AutoOmittingModel(choices, ignores_choice=ignores_choice)
        return model_factory(tokens, **kwargs)

    runtime.context = replace(runtime.context, model_factory=factory)
    return choices


async def test_complete_user_request_requires_real_tool_preview(workflow_runtime, model_factory, tmp_path, session_factory):
    choices = await configure(workflow_runtime, model_factory, tmp_path, session_factory)
    result = await run_turn(workflow_runtime, TurnRequest(
        message='订单1001的耳机左侧没有声音，请帮我建售后工单。', session_id='force-preview'))
    assert result.status == 'waiting_for_ticket'
    assert result.ticket_preview['description'] == '耳机左侧没有声音'
    assert choices == ['create_ticket']
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(Ticket)) == 0


async def test_provider_ignoring_required_call_cannot_claim_submission(workflow_runtime, model_factory, tmp_path, session_factory):
    await configure(workflow_runtime, model_factory, tmp_path, session_factory, ignores_choice=True)
    result = await run_turn(workflow_runtime, TurnRequest(
        message='耳机左侧没有声音，请帮我建售后工单。', session_id='provider-ignores'))
    assert result.stop_reason == 'invalid_decision'
    assert result.ticket_preview is None
    assert '未能完成' in result.answer
    assert not any(kind == 'answer' for kind, _ in model_factory.requests)


async def test_incomplete_request_asks_instead_of_claiming_progress(workflow_runtime, model_factory, tmp_path, session_factory):
    choices = await configure(workflow_runtime, model_factory, tmp_path, session_factory)
    result = await run_turn(workflow_runtime, TurnRequest(message='帮我建个工单', session_id='force-missing'))
    assert result.stop_reason == 'clarification'
    assert '问题描述' in result.answer
    assert result.ticket_preview is None
    assert choices == [None]
    assert not any(kind == 'answer' for kind, _ in model_factory.requests)


@pytest.mark.parametrize('question', ['耳机坏了', '不要建售后工单，耳机坏了', '如果耳机坏了，请帮我建售后工单'])
async def test_no_explicit_request_keeps_auto_selection(question, workflow_runtime, model_factory, tmp_path, session_factory):
    choices = await configure(workflow_runtime, model_factory, tmp_path, session_factory)
    model_factory.intents = ['物流查询']
    result = await run_turn(workflow_runtime, TurnRequest(message=question, session_id='ordinary'))
    assert result.ticket_preview is None
    assert 'create_ticket' not in choices
