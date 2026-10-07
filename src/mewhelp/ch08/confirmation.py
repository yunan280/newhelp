"""JSON 预览与服务器侧授权验证；不接受前端改写工单参数。"""
from copy import deepcopy
from uuid import uuid4

from mewhelp.tools.contracts import PreparedToolCall, ToolCallContext, ToolSnapshot, WriteAuthorization
from mewhelp.tools.permissions import arguments_hash, permission_error

from .schemas import TicketResumeRequest


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
