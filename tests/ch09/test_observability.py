import asyncio
import json
from contextlib import aclosing
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from langchain_core.runnables import RunnableLambda
from langfuse.langchain import CallbackHandler


def metadata(span):
    prefix = 'langfuse.observation.metadata.'
    return {key[len(prefix):]: value for key, value in span.attributes.items()
            if key.startswith(prefix)}


def trace_id(span):
    return format(span.context.trace_id, '032x')


@pytest.mark.asyncio
async def test_one_callback_parallel_roots_and_late_intent(ch09_module, span_client):
    module = ch09_module('observability')
    context = ch09_module('contracts').RequestTraceContext
    client, exporter, public_key = span_client
    factory = Mock(wraps=CallbackHandler)
    runtime = module.ObservationRuntime(client=client, public_key=public_key, callback_factory=factory)

    async def call(session, intent):
        with runtime.request(context(session_id=session, user_id='u', turn_id=session,
                                     entry_point='agent'), input={'question': session}) as root:
            await RunnableLambda(lambda value: {'retrieved': value}).ainvoke(
                session, config={'callbacks': list(runtime.callbacks)})
            await asyncio.sleep(0)
            root.set_intent(intent)
            root.finish(status='completed', output={'answer': session})
            return root.trace_id

    left, right = await asyncio.gather(call('s1', '知识咨询'), call('s2', '物流查询'))
    runtime.flush()
    assert left != right
    assert factory.call_count == 1
    roots = [s for s in exporter.get_finished_spans() if s.parent is None]
    assert len(roots) == 2
    assert {trace_id(s): metadata(s)['intent'] for s in roots} == {
        left: '知识咨询', right: '物流查询'}
    children = [s for s in exporter.get_finished_spans() if s.parent is not None]
    assert {trace_id(s) for s in children} == {left, right}


@pytest.mark.asyncio
async def test_background_trace_is_detached(ch09_module, span_client):
    runtime = ch09_module('observability').ObservationRuntime(client=span_client[0], public_key=span_client[2])
    context = ch09_module('contracts').RequestTraceContext

    async def background():
        with runtime.request(context(trace_kind='flywheel', entry_point='worker'),
                             input={'pool_id': '99'}) as child:
            child.finish(status='completed', output={})
            return child.trace_id

    with runtime.request(context(session_id='chat', turn_id='turn'), input={}) as root:
        background_id = await asyncio.create_task(background())
        root_id = root.trace_id
    runtime.flush()
    spans = span_client[1].get_finished_spans()
    assert root_id != background_id
    assert len(spans) == 2
    assert all(s.parent is None for s in spans)
    assert {metadata(s)['trace_kind'] for s in spans} == {'chat', 'flywheel'}


def test_disconnect_and_resume_close_distinct_roots(ch09_module, span_client):
    runtime = ch09_module('observability').ObservationRuntime(client=span_client[0], public_key=span_client[2])
    context = ch09_module('contracts').RequestTraceContext
    with runtime.request(context(session_id='s', turn_id='t'), input={}) as waiting:
        waiting.finish(status='waiting', output={'status': 'waiting_for_ticket'})
    with runtime.request(context(session_id='s', turn_id='t', entry_point='ticket_resume',
                                 origin_trace_id=waiting.trace_id), input={}) as resumed:
        resumed.finish(status='completed', output={'answer': '已取消'})
    with pytest.raises(GeneratorExit), runtime.request(context(session_id='s', turn_id='next'), input={}):
        raise GeneratorExit()
    runtime.flush()
    roots = span_client[1].get_finished_spans()
    assert len(roots) == 3
    assert waiting.trace_id != resumed.trace_id
    assert [metadata(s)['status'] for s in roots] == ['waiting', 'completed', 'disconnected']
    assert metadata(roots[1])['origin_trace_id'] == waiting.trace_id
    assert metadata(roots[1])['turn_id'] == 't'


def test_export_failure_keeps_business_exception_and_result(ch09_module):
    client = Mock()
    client.start_as_current_observation.side_effect = RuntimeError('export unavailable')
    runtime = ch09_module('observability').ObservationRuntime(client=client,
                                                             callback_factory=Mock())
    context = ch09_module('contracts').RequestTraceContext
    with runtime.request(context(), input={}) as root:
        root.set_intent('知识咨询')
        root.finish(status='completed', output={'answer': '业务正常'})
    with pytest.raises(ValueError, match='business error'), runtime.request(context(), input={}):
        raise ValueError('business error')


@pytest.mark.parametrize('event,status', [('done', 'completed'), ('waiting_for_ticket', 'waiting')])
async def test_json_consumer_closes_after_terminal_event_without_losing_trace(ch09_module, span_client, event, status):
    module = ch09_module('observability')
    runtime = module.ObservationRuntime(client=span_client[0], public_key=span_client[2])

    @module.trace_graph_stream('agent')
    async def stream(workflow, request, *, entry_point):
        yield {'event': event, 'data': {'intent': '售后', 'answer': '实际结果'}}

    workflow = SimpleNamespace(context=SimpleNamespace(observation_runtime=runtime))
    request = SimpleNamespace(session_id='json-session', resolved_user_id='u', model_dump=lambda **_: {})
    async with aclosing(stream(workflow, request, entry_point='agent')) as events:
        async for item in events:
            if item['event'] == event:
                break
    runtime.flush()
    roots = span_client[1].get_finished_spans()
    assert len(roots) == 1
    assert metadata(roots[0])['status'] == status
    assert json.loads(roots[0].attributes['langfuse.observation.output'])['answer'] == '实际结果'


async def test_legacy_prepare_binds_committed_session_before_model_io(ch09_module, span_client, ch09_db):
    from sqlalchemy import select

    from mewhelp.ch02 import service
    from mewhelp.db.models import Conversation

    module = ch09_module('observability')
    runtime = module.ObservationRuntime(client=span_client[0], public_key=span_client[2])
    ctx = ch09_module('contracts').RequestTraceContext(user_id='u', entry_point='ch02_agent')
    with runtime.request(ctx, input={}) as root:
        async with aclosing(service._prepare_turn_events(
                ch09_db, session_id='generated-session', user_id='u', message='查物流', sink=service._Sink())) as events:
            await anext(events)  # Session has committed; no model request needed.
        with ch09_db() as db:
            cid = db.scalar(select(Conversation.id).where(Conversation.session_id == 'generated-session'))
        assert root.metadata['session_id'] == 'generated-session'
        assert root.metadata['conversation_id'] == cid


@pytest.mark.parametrize('kind', ['order', 'ticket'])
async def test_new_message_auto_cancel_has_separate_old_turn_root(ch09_module, span_client, monkeypatch, kind):
    from mewhelp.ch06 import selection
    from mewhelp.ch08 import confirmation

    module = ch09_module('observability')
    runtime = module.ObservationRuntime(client=span_client[0], public_key=span_client[2])
    old = {'session_id': 's', 'user_id': 'u', 'conversation_id': 1, 'turn_id': 'old-turn',
           'trace_id': 'old-origin', 'intent': '售后'}
    seen = []

    class Graph:
        async def aget_state(self, config):
            return SimpleNamespace(values=old)

        async def ainvoke(self, *args, **kwargs):
            root = module.current_request()
            seen.append((root.trace_id, dict(root.metadata)))
            return {'status': 'completed'}

    workflow = SimpleNamespace(graph=Graph(), context=SimpleNamespace(observation_runtime=runtime, tool_runtime=None))
    monkeypatch.setattr(selection, 'active_selection', lambda snapshot: {'selection_id': 'selection'})
    monkeypatch.setattr(confirmation, 'active_ticket_preview', lambda snapshot: {'confirmation_id': 'confirm'})
    ctx = ch09_module('contracts').RequestTraceContext(session_id='s', turn_id='new-turn')
    with runtime.request(ctx, input={}) as parent:
        if kind == 'order':
            await selection.cancel_pending_locked(workflow, {})
        else:
            await confirmation.cancel_ticket_locked(workflow, {}, '用户发送新消息')
        assert module.current_request() is parent
    assert seen[0][0] != parent.trace_id
    assert seen[0][1]['turn_id'] == 'old-turn'
    assert seen[0][1]['origin_trace_id'] == 'old-origin'
    runtime.flush()
    assert len([s for s in span_client[1].get_finished_spans() if s.parent is None]) == 2
