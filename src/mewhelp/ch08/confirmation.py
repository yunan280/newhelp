"""JSON 预览与服务器侧授权验证；不接受前端改写工单参数。"""
import asyncio
import time
from contextlib import aclosing
from copy import deepcopy
from dataclasses import asdict, replace
from uuid import uuid4

from langchain_core.messages import ToolMessage
from langgraph.types import Command, interrupt

from mewhelp.tools.contracts import (
    PreparedToolCall,
    ToolCallContext,
    ToolResult,
    ToolSnapshot,
    WriteAuthorization,
)
from mewhelp.tools.permissions import arguments_hash, permission_error

from .schemas import TicketResumeRequest
from mewhelp.ch09.observability import current_request, trace_graph_stream


def make_ticket_preview(prepared: PreparedToolCall, context: ToolCallContext) -> dict:
    if not prepared.requires_confirmation or prepared.name != 'create_ticket':
        raise ValueError('只有待确认建单调用可产生预览')
    if context.conversation_id is None or not context.session_id or not context.user_id:
        raise ValueError('预览缺少可信用户/会话上下文')
    return {'confirmation_id': uuid4().hex, 'tool_call_id': prepared.tool_call_id,
            'tool_name': prepared.name, 'args': deepcopy(prepared.args),
            'description': prepared.args['description'], 'ticket_type': prepared.args['ticket_type'],
            'args_hash': prepared.args_hash, 'schema_hash': prepared.schema_hash,
            'conversation_id': context.conversation_id, 'session_id': context.session_id,
            'user_id': context.user_id, 'turn_id': context.turn_id}


def verify_ticket_confirmation(state: dict, request: TicketResumeRequest,
                               snapshot: ToolSnapshot) -> WriteAuthorization:
    preview = state.get('ticket_preview')
    if not preview or state.get('ticket_status') != 'pending' or preview['confirmation_id'] != request.confirmation_id:
        raise ValueError('工单预览已经失效')
    if (state.get('session_id') != request.session_id or state.get('user_id') != request.resolved_user_id
        or preview['session_id'] != request.session_id or preview['user_id'] != request.resolved_user_id
        or preview['conversation_id'] != state.get('conversation_id')):
        raise ValueError('工单确认不属于当前用户/会话')
    if arguments_hash(preview['args']) != preview['args_hash']:
        raise ValueError('预览参数已改变，须重新确认')
    spec = snapshot.get('create_ticket')
    if spec is None or arguments_hash(spec.input_schema) != preview['schema_hash']:
        raise ValueError('建单工具 Schema 已改变，须重新确认')
    auth = WriteAuthorization('preview', request.confirmation_id, preview['conversation_id'],
                              request.session_id, request.resolved_user_id, 'create_ticket', preview['args_hash'])
    context = ToolCallContext(preview['conversation_id'], request.session_id,
                              request.resolved_user_id, authorization=auth)
    reason = permission_error(spec, preview['args'], context)
    if reason:
        raise ValueError(reason)
    return auth


class TicketConfirmationError(ValueError):
    def __init__(self, message, status_code=409):
        super().__init__(message)
        self.status_code = status_code


def _execution(context):
    from mewhelp.ch05.agent import registry_for_context
    from mewhelp.tools.engine import ToolExecutionEngine
    registry = registry_for_context(context)
    return registry.engine or ToolExecutionEngine(), registry.snapshot()


def _observe(state, context, emit, call, result):
    from mewhelp.ch07.context import tag_message
    item = {'call_id': call['id'], 'round': state['decision_count'], 'name': result.name,
            'args': result.args, 'ok': result.ok, 'content': result.content, 'error': result.error,
            'elapsed_ms': result.elapsed_ms}
    emit({'event': 'tool', 'data': {'phase': 'end', **item}})
    observation = ToolMessage(content=result.content, tool_call_id=call['id'], name=result.name,
        status='success' if result.ok else 'error', id=state['turn_id'] + '-result-' + call['id'])
    cursor = state.get('tool_cursor', 0) + 1
    queue = state.get('tool_queue') or state['pending_tool_calls']
    return {'agent_messages': [*state['agent_messages'], observation],
            'tool_trace': [*state['tool_trace'], item], 'tool_count': state['tool_count'] + 1,
            'messages': [tag_message(observation, turn_id=state['turn_id'])],
            'tool_cursor': cursor, 'tool_results': [*state.get('tool_results', []), item],
            'pending_tool_calls': queue[cursor:]}


async def prepare_ticket_node(state, context, emit):
    """一个节点完成一个调用，checkpoint 后才进入下一调用或等待。"""
    from mewhelp.ch05.agent import remaining, stopped, tool_call_context
    from mewhelp.ch05.events import event
    from mewhelp.ch05.workflow import _committed_turn_messages, _write_ledger
    if remaining(state, context) <= 0:
        return stopped('deadline')
    queue = state.get('tool_queue') or state['pending_tool_calls']
    call = queue[state.get('tool_cursor', 0)]
    engine, snapshot = _execution(context)
    call_context = tool_call_context(state, context, tool_call_id=call['id'])
    emit({'event': 'tool', 'data': {'phase': 'start', 'call_id': call['id'],
         'round': state['decision_count'], 'name': call['name'], 'args': call['args']}})
    prepared = await engine.prepare(snapshot, call['name'], call['args'], call_context)
    if prepared.requires_confirmation:
        preview = make_ticket_preview(prepared, call_context)
        update = {'ticket_preview': preview, 'ticket_prepared': asdict(prepared),
                  'ticket_status': 'pending', 'status': 'waiting_for_ticket',
                  'stop_reason': 'waiting_for_ticket', 'answer': '请核对工单预览后确认提交，或取消。'}
        ids = await asyncio.to_thread(_write_ledger, context, {**state, **update}, phase='waiting_ticket')
        update['messages'] = _committed_turn_messages({**state, **update}, ids, suffix='waiting-ticket')
        emit(event('token', text=update['answer']))
        return update
    result = prepared.result or await engine.execute(snapshot, call['name'], call['args'], call_context)
    update = _observe(state, context, emit, call, result)
    from mewhelp.ch07.tokens import estimate_text
    if estimate_text(result.content, profile=context.profile) > context.settings.tool_result_max_tokens:
        update.update({**stopped('tool_result_limit'), 'answer': '查询结果超过本轮处理容量，请缩小范围或联系人工。'})
    return update


async def await_ticket_node(state, context, emit):
    """节点重放只接收输入；预览、数据库、审计副作用均在别的节点。"""
    preview = state['ticket_preview']
    value = interrupt({'kind': 'ticket_preview', **preview})
    if (not isinstance(value, dict) or value.get('confirmation_id') != preview['confirmation_id']
        or value.get('action') not in ('confirm', 'cancel')):
        raise TicketConfirmationError('工单确认输入已失效')
    return {'ticket_resume': value, 'ticket_status': 'received', 'status': 'completed',
            'stop_reason': '', 'answer': '', 'started_at': time.time()}


def _receipt(context, state, preview):
    from mewhelp.db.models import Conversation
    from mewhelp.db.repository import find_ticket_by_request_id
    with context.session_factory() as db:
        owner = db.get(Conversation, state['conversation_id'])
        if owner is None or owner.session_id != state['session_id'] or owner.user_id != state['user_id']:
            raise TicketConfirmationError('会话归属不匹配', 403)
        row = find_ticket_by_request_id(db, request_id=preview['confirmation_id'])
        if row is None:
            return None
        if row.conversation_id != state['conversation_id'] or row.description != preview['args']['description'] or row.ticket_type.value != preview['args']['ticket_type']:
            raise TicketConfirmationError('确认幂等键的工单参数不一致')
        return {'ticket_no': row.ticket_no, 'ticket_type': row.ticket_type.value,
                'confirmation_id': preview['confirmation_id']}


async def execute_confirmed_ticket_node(state, context, emit):
    from mewhelp.ch05.agent import tool_call_context
    from mewhelp.ch05.events import event
    from mewhelp.ch05.service import result_from_state
    preview, resumed = state['ticket_preview'], state['ticket_resume']
    prepared = PreparedToolCall(**state['ticket_prepared'])
    call = {'id': prepared.tool_call_id, 'name': prepared.name, 'args': prepared.args}
    engine, snapshot = _execution(context)
    if context.tool_runtime is not None:
        snapshot = await context.tool_runtime.refresh()
    call_context = tool_call_context(state, context, tool_call_id=prepared.tool_call_id)
    receipt = None
    if resumed['action'] == 'cancel':
        result = await engine.reject(prepared, call_context, resumed.get('reason') or '用户取消了本次工单提交。')
        status = 'cancelled'
    else:
        # A committed receipt is historical fact; current rules only gate new writes.
        receipt = await asyncio.to_thread(_receipt, context, state, preview)
        if receipt:
            result = ToolResult('create_ticket', prepared.args, True,
                f"工单已提交，工单号 {receipt['ticket_no']}。", None, 0, 0,
                status='成功', tool_call_id=prepared.tool_call_id)
            await engine._finish(result, call_context)
            status = 'submitted'
        else:
            result, status, receipt = await _execute_new_ticket(
                state, context, preview, prepared, engine, snapshot, call_context)
    update = {**_observe(state, context, emit, call, result),
              'ticket_status': status, 'ticket_receipt': receipt, 'ticket_preview': None,
              'ticket_prepared': None, 'ticket_resume': None}
    stable = f"工单已提交，工单号 {receipt['ticket_no']}。" if receipt else result.content
    public = result_from_state({**state, **update, 'answer': stable, 'status': 'completed',
                               'stop_reason': 'ticket_created' if receipt else 'cancelled' if status == 'cancelled' else 'ticket_failed'})
    record = {'action': resumed['action'], 'preview': preview, 'status': status,
              'receipt': receipt, 'result': public.model_dump(mode='json')}
    update['ticket_confirmation_receipts'] = {**state.get('ticket_confirmation_receipts', {}),
                                              preview['confirmation_id']: record}
    if receipt:
        emit(event('ticket_receipt', **receipt))
    if resumed.get('abandon') or status in ('cancelled', 'denied', 'unknown', 'failed'):
        # Abandoned queued requests receive terminal audits without executing side effects.
        for queued in update['pending_tool_calls']:
            queued_context = tool_call_context(state, context, tool_call_id=queued['id'])
            pending = await engine.prepare(snapshot, queued['name'], queued['args'], queued_context)
            await engine.reject(pending, queued_context, '待确认批次已结束，本次调用不执行。')
        update.update({'pending_tool_calls': [], 'stop_reason': public.stop_reason, 'answer': stable})
    return update


async def _execute_new_ticket(state, context, preview, prepared, engine, snapshot, call_context):
    request = TicketResumeRequest(session_id=state['session_id'], user_id=state['user_id'],
                                  confirmation_id=preview['confirmation_id'], action='confirm')
    try:
        auth = verify_ticket_confirmation({**state, 'ticket_status': 'pending'}, request, snapshot)
    except ValueError as exc:
        result = await engine.reject(prepared, call_context, str(exc))
        return result, 'denied', None
    result = await engine.execute(snapshot, 'create_ticket', prepared.args,
                                 replace(call_context, authorization=auth))
    receipt = await asyncio.to_thread(_receipt, context, state, preview) if result.ok else None
    status = 'submitted' if receipt else 'unknown' if result.status == '超时' else 'failed'
    return result, status, receipt


def active_ticket_preview(snapshot) -> dict | None:
    preview = snapshot.values.get('ticket_preview')
    if not preview or snapshot.values.get('ticket_status') != 'pending':
        return None
    for task in snapshot.tasks:
        if task.error:
            continue
        for item in task.interrupts:
            value = item.value
            if isinstance(value, dict) and value.get('kind') == 'ticket_preview' and value.get('confirmation_id') == preview['confirmation_id']:
                return preview
    return None


async def pending_ticket(runtime, session_id, user_id):
    from mewhelp.ch05.service import result_from_state
    from mewhelp.ch06.selection import _check_owner
    async with runtime.locks.lock(session_id):
        if not await asyncio.to_thread(_check_owner, runtime.context, session_id, user_id):
            return None
        snapshot = await runtime.graph.aget_state({'configurable': {'thread_id': session_id}})
        return result_from_state(snapshot.values) if active_ticket_preview(snapshot) else None


async def readonly_ticket_receipt(runtime, session_id, user_id, confirmation_id):
    """刷新仅读取已处理回执，绝不恢复图或再次执行写操作。"""
    from mewhelp.ch05.schemas import TurnResult
    from mewhelp.ch06.selection import _check_owner
    async with runtime.locks.lock(session_id):
        if not await asyncio.to_thread(_check_owner, runtime.context, session_id, user_id):
            return None
        snapshot = await runtime.graph.aget_state({'configurable': {'thread_id': session_id}})
        state = snapshot.values
        remembered = state.get('ticket_confirmation_receipts', {}).get(confirmation_id)
        if not remembered:
            return None
        result = TurnResult.model_validate(remembered['result'])
        if remembered['status'] == 'unknown':
            receipt = await asyncio.to_thread(_receipt, runtime.context, state, remembered['preview'])
            if receipt:
                result = result.model_copy(update={'ticket_receipt': receipt,
                    'answer': f"工单已提交，工单号 {receipt['ticket_no']}。", 'stop_reason': 'ticket_created'})
        return result


async def cancel_ticket_locked(runtime, config, reason):
    snapshot = await runtime.graph.aget_state(config)
    preview = active_ticket_preview(snapshot)
    if preview:
        context = runtime.context
        if context.tool_runtime is not None:
            context = replace(context, tool_snapshot=await context.tool_runtime.refresh())
        await runtime.graph.ainvoke(Command(resume={'action': 'cancel', 'abandon': True,
            'confirmation_id': preview['confirmation_id'], 'reason': reason}), config,
            context=context, durability='sync')


@trace_graph_stream('ticket_resume')
async def stream_ticket_resume(runtime, request):
    from mewhelp.ch05.events import event
    from mewhelp.ch05.schemas import TurnResult
    from mewhelp.ch05.service import result_from_state
    from mewhelp.ch06.selection import _check_owner
    from mewhelp.ch07.context import prepare_request_context
    config = {'configurable': {'thread_id': request.session_id}, 'recursion_limit': 80}
    async with runtime.locks.lock(request.session_id):
        if not await asyncio.to_thread(_check_owner, runtime.context, request.session_id, request.resolved_user_id):
            raise TicketConfirmationError('会话不存在', 404)
        snapshot = await runtime.graph.aget_state(config)
        state = snapshot.values
        root = current_request()
        if root:
            root.bind(turn_id=state.get('turn_id'), origin_trace_id=state.get('trace_id'),
                      conversation_id=state.get('conversation_id'))
            root.set_intent(state.get('intent', '其他'))
        if state.get('user_id') != request.resolved_user_id:
            raise TicketConfirmationError('会话属于其他用户', 403)
        remembered = state.get('ticket_confirmation_receipts', {}).get(request.confirmation_id)
        yield event('session', session_id=request.session_id, conversation_id=state['conversation_id'], resumed=True)
        if remembered:
            if remembered['action'] != request.action:
                raise TicketConfirmationError('本次确认已处理，不能更换操作')
            result = TurnResult.model_validate(remembered['result'])
            # Timeout recovery only reads a receipt, never reissues the write.
            if remembered['status'] == 'unknown':
                receipt = await asyncio.to_thread(_receipt, runtime.context, state, remembered['preview'])
                if receipt:
                    result = result.model_copy(update={'ticket_receipt': receipt,
                        'answer': f"工单已提交，工单号 {receipt['ticket_no']}。", 'stop_reason': 'ticket_created'})
            if result.ticket_receipt:
                yield event('ticket_receipt', **result.ticket_receipt)
            yield event('token', text=result.answer)
            yield event('done', **result.model_dump(mode='json'), finish_reason=result.stop_reason)
            return
        preview = active_ticket_preview(snapshot)
        recovery = (state.get('ticket_status') == 'received'
                    and snapshot.next == ('execute_confirmed_ticket',))
        if recovery:
            resumed = state.get('ticket_resume') or {}
            if resumed.get('confirmation_id') != request.confirmation_id or resumed.get('action') != request.action:
                raise TicketConfirmationError('已接收的确认不能更换操作')
            preview = state.get('ticket_preview')
        if preview is None or preview['confirmation_id'] != request.confirmation_id:
            raise TicketConfirmationError('工单卡片已失效')
        context = await prepare_request_context(runtime.context, state)
        reason = None
        if request.action == 'confirm':
            try:
                verify_ticket_confirmation(state, request, context.tool_snapshot)
            except ValueError as exc:
                reason = str(exc)
        incoming = {'action': 'cancel' if reason else request.action,
                    'confirmation_id': request.confirmation_id}
        if reason:
            incoming['reason'] = reason
        if recovery:
            await runtime.graph.aupdate_state(config, {'started_at': time.time()}, as_node='await_ticket')
        graph_input = None if recovery else Command(resume=incoming)
        async with asyncio.timeout(runtime.context.limits.turn_seconds):
            async with aclosing(runtime.graph.astream(graph_input, config,
                context=context, stream_mode='custom', durability='sync')) as stream:
                async for item in stream:
                    yield item
        final = await runtime.graph.aget_state(config)
        if final.next:
            raise TicketConfirmationError('确认后的流程尚未完成')
        result = result_from_state(final.values)
        # Do not create a receipt checkpoint over another active interrupt.
        receipts = dict(final.values.get('ticket_confirmation_receipts', {}))
        if request.confirmation_id in receipts:
            receipts[request.confirmation_id] = {**receipts[request.confirmation_id],
                'action': request.action, 'result': result.model_dump(mode='json')}
            await runtime.graph.aupdate_state(config, {'ticket_confirmation_receipts': receipts}, as_node='log_turn')
        yield event('done', **result.model_dump(mode='json'), finish_reason=result.stop_reason)


async def resume_ticket(runtime, request):
    from mewhelp.ch05.schemas import TurnResult
    async with aclosing(stream_ticket_resume(runtime, request)) as stream:
        async for item in stream:
            if item['event'] == 'done':
                fields = dict(item['data'])
                fields.pop('finish_reason', None)
                return TurnResult.model_validate(fields)
    raise TicketConfirmationError('确认流程没有终态')
