from importlib import import_module

import pytest
from langchain_core.messages import AIMessage

from mewhelp.ch05.schemas import TurnRequest
from mewhelp.ch05.service import run_turn
from mewhelp.db.models import Message, MsgRole


def context_module():
    try:
        return import_module('mewhelp.ch07.context')
    except ModuleNotFoundError:
        pytest.fail('Ch07 must carry a separate projected snapshot through the full State')


async def test_full_tool_history_is_auditable_but_hidden_from_visible_history(
        workflow_runtime, model_factory, session_factory):
    model_factory.intents = ['物流']
    model_factory.decisions = [AIMessage('', tool_calls=[{'id': 'raw-call',
        'name': 'query_logistics', 'args': {'order_id': '1001'}, 'type': 'tool_call'}])]
    result = await run_turn(workflow_runtime, TurnRequest(message='订单1001物流', session_id='raw'))
    snapshot = await workflow_runtime.graph.aget_state({'configurable': {'thread_id': 'raw'}})
    assert any(m.type == 'tool' and m.tool_call_id == 'raw-call' for m in snapshot.values['messages'])
    assert sum(m.type == 'human' and m.content == '订单1001物流' for m in snapshot.values['messages']) == 1
    with session_factory() as session:
        rows = session.query(Message).filter_by(conversation_id=result.conversation_id).all()
        assert [r.role for r in rows] == [MsgRole.user, MsgRole.assistant, MsgRole.tool, MsgRole.assistant]
        assert rows[1].tool_calls[0]['id'] == rows[2].tool_call_id == 'raw-call'
        from mewhelp.ch07.store import read_visible_messages
        visible = read_visible_messages(session, conversation_id=result.conversation_id, user_id='demo-user')
        assert [r.role for r in visible.messages] == ['user','assistant']


async def test_failed_turn_retained_for_diagnostics_not_replayed(workflow_runtime, model_factory):
    context_module()
    model_factory.fail_classifier = True
    with pytest.raises(RuntimeError):
        await run_turn(workflow_runtime, TurnRequest(message='坏的问题', session_id='failed-ch07'))
    model_factory.fail_classifier = False
    await run_turn(workflow_runtime, TurnRequest(message='你好', session_id='failed-ch07'))
    snapshot = await workflow_runtime.graph.aget_state({'configurable': {'thread_id': 'failed-ch07'}})
    assert any(m.content == '坏的问题' for m in snapshot.values['messages'])
    payload = snapshot.values['history_ctx']
    assert '坏的问题' not in str(payload['layer1']) + str(payload['layer2'])


async def test_request_context_is_private_and_codec_roundtrips(workflow_runtime):
    module = context_module()
    result = await run_turn(workflow_runtime, TurnRequest(message='你好', session_id='private'))
    snapshot = await workflow_runtime.graph.aget_state({'configurable': {'thread_id': 'private'}})
    state = {**snapshot.values, 'turn_id': 'next'}
    before = workflow_runtime.context.request_epoch
    copied = await module.prepare_request_context(workflow_runtime.context, state)
    assert copied is not workflow_runtime.context and workflow_runtime.context.request_epoch == before
    history = module.history_from_payload(copied.request_history)
    assert history.conversation_id == result.conversation_id and len(history.layer1) == 2
    assert module.history_payload(history) == copied.request_history


async def test_understanding_and_classifier_use_identical_snapshot(workflow_runtime, model_factory):
    await run_turn(workflow_runtime, TurnRequest(message='你好', session_id='shared'))
    await run_turn(workflow_runtime, TurnRequest(message='谢谢', session_id='shared'))
    understanding = [messages for kind, messages in model_factory.requests if kind == 'understanding'][-1]
    classifier = [messages for kind, messages in model_factory.requests if kind == 'classifier'][-1]
    assert [(m.type, m.content) for m in understanding[1:-1]] == [
        (m.type, m.content) for m in classifier[1:-1]]
    assert len(understanding[1:-1]) == 3


async def test_checkpoint_reopen_preserves_tool_pair_and_numeric_anchors(
        session_factory, checkpoint_settings, model_factory, router_settings):
    from mewhelp.ch05.runtime import open_runtime
    model_factory.intents = ['物流']
    model_factory.decisions = [AIMessage('', tool_calls=[{'id': 'reopen-call',
        'name': 'query_logistics', 'args': {'order_id': '1001'}, 'type': 'tool_call'}])]
    async with open_runtime(session_factory, settings=checkpoint_settings,
        model_factory=model_factory, router_settings=router_settings,
        router_model_factory=model_factory.router) as runtime:
        await run_turn(runtime, TurnRequest(message='订单1001查物流', session_id='reopen-ch07'))
    async with open_runtime(session_factory, settings=checkpoint_settings,
        model_factory=model_factory, router_settings=router_settings,
        router_model_factory=model_factory.router) as runtime:
        await run_turn(runtime, TurnRequest(message='谢谢', session_id='reopen-ch07'))
        snapshot = await runtime.graph.aget_state({'configurable': {'thread_id': 'reopen-ch07'}})
        raw = snapshot.values['messages']
        assert sum(m.type == 'tool' for m in raw) == 1
        history = context_module().history_from_payload(snapshot.values['history_ctx'])
        assert any(m.type == 'tool' for m in history.layer1)
        assert history.turns[0].from_msg_id > 0 and history.turns[0].upto_msg_id > 0
