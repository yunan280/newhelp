"""审计尽力写入独立 Session；失败不改变调用结果。"""
import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from sqlalchemy import select

from mewhelp.db.models import ToolAuditLog

from .contracts import ToolCallContext, ToolResult

logger = logging.getLogger(__name__)


class ToolAuditWriter:
    def __init__(self, session_factory, *, timeout_seconds: float = 1.0):
        self.session_factory = session_factory
        self.timeout_seconds = timeout_seconds
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='tool-audit')
        self._busy = Lock()

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
                error_message=(f'{result.error}: {result.content}'[:512] if result.error else None),
                retry_count=min(255, max(0, result.retry_count)), duration_ms=max(0, result.elapsed_ms)))
            db.commit()

    async def record(self, result: ToolResult, context: ToolCallContext) -> None:
        # Timeout cannot stop a running database thread. Do not queue unbounded
        # writes or consume the default executor used by business handlers.
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout_seconds
        while not self._busy.acquire(blocking=False):
            if loop.time() >= deadline:
                logger.warning('工具审计线程等待超时；业务结果保持原样')
                return
            await asyncio.sleep(min(.001, max(0, deadline - loop.time())))
        try:
            future = loop.run_in_executor(
                self._executor, self._guarded_record, result, context)
        except Exception:
            self._busy.release()
            logger.warning('工具审计提交失败；业务结果保持原样', exc_info=True)
            return
        try:
            await asyncio.wait_for(asyncio.shield(future), max(0, deadline - loop.time()))
        except Exception:
            logger.warning('工具审计写入失败；业务结果保持原样', exc_info=True)

    def _guarded_record(self, result, context):
        try:
            self._record(result, context)
        except Exception:
            logger.warning('工具审计数据库写入失败；业务结果保持原样', exc_info=True)
        finally:
            self._busy.release()

    def close(self):
        self._executor.shutdown(wait=False)
