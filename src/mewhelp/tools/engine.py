"""唯一执行引擎：Schema → 本地权限 → 有界执行 → 分诊 → 最终审计。"""
import asyncio
import logging
import time
from dataclasses import replace
from uuid import uuid4

from langgraph.errors import GraphInterrupt

from .contracts import PreparedToolCall, ToolCallContext, ToolResult, ToolSnapshot
from .errors import classify_failure
from .formatting import format_result
from .permissions import arguments_hash, permission_error
from .validation import validate_arguments

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
BACKOFF_SECONDS = (.2, .4)


class ToolExecutionEngine:
    def __init__(self, audit=None, *, sleep=asyncio.sleep):
        self.audit, self.sleep = audit, sleep

    async def _finish(self, result, context):
        if self.audit is not None:
            try:
                await self.audit.record(result, context)
            except Exception:
                logger.warning('审计失败，保留工具结果', exc_info=True)
        return result

    def _result(self, snapshot, name, args, context, *, content, error, status,
                attempts=0, elapsed_ms=0, artifact=None):
        spec = snapshot.get(name)
        return ToolResult(name, args, status == '成功', content, error, elapsed_ms, attempts,
                          artifact, status, max(0, attempts - 1),
                          spec.source if spec else 'builtin', spec.mcp_server if spec else None,
                          context.tool_call_id)

    async def prepare(self, snapshot: ToolSnapshot, name: str, args: dict,
                      context: ToolCallContext) -> PreparedToolCall:
        context = replace(context, tool_call_id=context.tool_call_id or uuid4().hex)
        spec = snapshot.get(name)
        schema_hash = arguments_hash(spec.input_schema) if spec else ''
        prepared = PreparedToolCall(name, args.copy(), context.tool_call_id,
                                    arguments_hash(args), schema_hash, False)
        error, content, status = None, '', ''
        if spec is None:
            error, status = 'unknown_tool', '失败'
            content = f"没有名为 {name} 的工具。可用的工具有:{', '.join(s.tool.name for s in snapshot.specs.values() if s.permission != 'deny' and s.available)}。"
        else:
            errors = validate_arguments(spec.input_schema, args)
            if errors:
                error, status = 'invalid_args', '校验拦下'
                content = '参数不合法，请补齐或修改后重新调用：' + '; '.join(f"{e['path']}: {e['message']}" for e in errors)
            else:
                reason = permission_error(spec, args, context)
                evidence = context.intent_evidence or {}
                if spec.tool.name == 'create_ticket' and context.authorization is None and evidence.get('explicit_request') is True:
                    from mewhelp.ch08.ticket_intent import validate_ticket_draft
                    draft_errors = validate_ticket_draft(args, evidence)
                    if draft_errors:
                        result = await self._finish(self._result(snapshot, name, args, context,
                            content='; '.join(draft_errors), error='invalid_args', status='校验拦下'), context)
                        return replace(prepared, result=result)
                if reason and spec.tool.name == 'create_ticket' and spec.permission == 'write' and spec.available and context.authorization is None and evidence.get('explicit_request') is True:
                    return replace(prepared, requires_confirmation=True)
                if reason:
                    error, content, status = 'permission_denied', reason, '权限拒绝'
        if error:
            result = await self._finish(self._result(snapshot, name, args, context,
                     content=content, error=error, status=status), context)
            return replace(prepared, result=result)
        return prepared

    async def reject(self, prepared: PreparedToolCall, context: ToolCallContext, reason: str) -> ToolResult:
        if prepared.result is not None:
            return prepared.result
        result = ToolResult(prepared.name, prepared.args, False, reason, 'permission_denied',
                            0, 0, status='权限拒绝', tool_call_id=prepared.tool_call_id)
        return await self._finish(result, context)

    async def execute(self, snapshot: ToolSnapshot, name: str, args: dict,
                      context: ToolCallContext) -> ToolResult:
        started = time.monotonic()
        prepared = await self.prepare(snapshot, name, args, context)
        context = replace(context, tool_call_id=prepared.tool_call_id)
        if prepared.result is not None:
            return prepared.result
        if prepared.requires_confirmation:
            return await self.reject(prepared, context, '建工单尚未收到用户的前端确认。')
        spec = snapshot.get(name)
        limit = MAX_ATTEMPTS if spec.retryable and spec.permission == 'readonly' else 1
        attempts, artifact, status, content, error = 0, None, '失败', '', None
        while attempts < limit:
            remaining = context.deadline_monotonic - time.monotonic() if context.deadline_monotonic else spec.timeout_seconds
            if remaining <= 0:
                status, error, content = '超时', 'TimeoutError', '工具执行总时限已到。'
                break
            attempts += 1
            try:
                target = spec.tool_factory(context) if spec.tool_factory else spec.tool
                invocation = ({'name': name, 'args': args, 'id': context.tool_call_id, 'type': 'tool_call'}
                              if target.response_format == 'content_and_artifact' else args)
                raw = await asyncio.wait_for(target.ainvoke(invocation), min(spec.timeout_seconds, remaining))
                ok, content, error, artifact = format_result(spec, raw)
                status = '成功' if ok else '失败'
                break
            except (asyncio.CancelledError, GraphInterrupt):
                result = self._result(snapshot, name, args, context, content='工具调用被中断。',
                    error='interrupted', status='失败', attempts=attempts,
                    elapsed_ms=int((time.monotonic() - started) * 1000))
                await asyncio.shield(self._finish(result, context))
                raise
            except Exception as exc:
                status, transient = classify_failure(exc)
                error = type(exc).__name__
                content = f'调用 {name} {"超时" if status == "超时" else "失败"}：{type(exc).__name__}: {exc}'
                if spec.permission == 'write' and status == '超时':
                    content += '。写操作可能已执行，结果未知；不会自动重试，请先核对回执。'
                if not transient or attempts >= limit:
                    break
                delay = BACKOFF_SECONDS[attempts - 1]
                if context.deadline_monotonic and time.monotonic() + delay >= context.deadline_monotonic:
                    status, error = '超时', 'TimeoutError'
                    break
                await self.sleep(delay)
        return await self._finish(self._result(snapshot, name, args, context,
            content=content, error=error, status=status, attempts=attempts, artifact=artifact,
            elapsed_ms=int((time.monotonic() - started) * 1000)), context)
