"""审计尽力写入独立 Session；失败不改变调用结果。"""
import asyncio
import json
import logging

from sqlalchemy import select

from mewhelp.db.models import ToolAuditLog

from .contracts import ToolCallContext, ToolResult

logger = logging.getLogger(__name__)


class ToolAuditWriter:
    def __init__(self, session_factory, *, timeout_seconds: float = 1.0):
        self.session_factory = session_factory
        self.timeout_seconds = timeout_seconds

    def existing_call(self, conversation_id: int | None, tool_call_id: str | None) -> bool:
        if not tool_call_id:
            return False
        with self.session_factory() as db:
            return db.scalar(select(ToolAuditLog.id).where(
                ToolAuditLog.conversation_id == conversation_id,
                ToolAuditLog.tool_call_id == tool_call_id).limit(1)) is not None

    def _record(self, result: ToolResult, context: ToolCallContext):
        if self.existing_call(context.conversation_id, result.tool_call_id):
            return
        args = json.loads(json.dumps(result.args, ensure_ascii=False, default=str))
        with self.session_factory() as db:
            db.add(ToolAuditLog(conversation_id=context.conversation_id,
                tool_call_id=result.tool_call_id, tool_name=result.name, tool_source=result.source,
                mcp_server=result.mcp_server, arguments=args, result_summary=result.content[:4000],
                status=result.status or ('成功' if result.ok else '失败'),
                error_message=(result.error[:512] if result.error else None),
                retry_count=min(255, max(0, result.retry_count)), duration_ms=max(0, result.elapsed_ms)))
            db.commit()

    async def record(self, result: ToolResult, context: ToolCallContext) -> None:
        try:
            await asyncio.wait_for(asyncio.to_thread(self._record, result, context),
                                   self.timeout_seconds)
        except Exception:
            logger.warning('工具审计写入失败；业务结果保持原样', exc_info=True)
