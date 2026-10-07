"""本地执行授权：外部声明和模型参数均不能提供写凭证。"""
import hashlib
import json

from .contracts import ToolCallContext, ToolSpec


def arguments_hash(args: dict) -> str:
    return hashlib.sha256(json.dumps(args, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def permission_error(spec: ToolSpec, args: dict, context: ToolCallContext) -> str | None:
    if not spec.available:
        return '工具来源当前不可用，请稍后再试。'
    if spec.permission == 'deny':
        return '工具未获得本地权限规则授权。'
    if spec.source == 'mcp' and spec.permission != 'readonly':
        return '本系统不授权外部 MCP 写操作。'
    if spec.permission == 'readonly':
        return None
    if spec.tool.name != 'create_ticket':
        return '本系统仅授权 create_ticket 写操作。'
    auth = context.authorization
    if auth is None:
        return '建工单尚未收到用户的前端确认。'
    if (auth.method not in ('preview', 'legacy_button') or not auth.confirmation_id
        or auth.tool_name != spec.tool.name or auth.args_hash != arguments_hash(args)
        or auth.conversation_id != context.conversation_id
        or auth.session_id != context.session_id or auth.user_id != context.user_id):
        return '确认凭据与本次用户、会话或参数不一致。'
    return None
