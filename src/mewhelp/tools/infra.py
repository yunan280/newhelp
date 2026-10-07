"""旧公开执行入口保留，所有调用委托统一工具引擎。"""
import asyncio

from langchain_core.tools import BaseTool

from .contracts import ToolCallContext, ToolResult, ToolSpec
from .engine import BACKOFF_SECONDS, MAX_ATTEMPTS, ToolExecutionEngine
from .formatting import TOOL_RESULT_MAX_CHARS, as_text as _as_text, truncate as _truncate

TOOL_TIMEOUT_SECONDS = 3.0


async def execute_tool(tool: BaseTool, args: dict, *, retryable: bool,
                       timeout: float = TOOL_TIMEOUT_SECONDS, preserve_raw: bool = False,
                       sleep=asyncio.sleep, context: ToolCallContext | None = None,
                       audit=None) -> ToolResult:
    from .registry import ToolRegistry
    spec = ToolSpec(tool, retryable, timeout, preserve_raw,
                    permission='write' if tool.name == 'create_ticket' else 'readonly')
    engine = ToolExecutionEngine(audit, sleep=sleep)
    return await engine.execute(ToolRegistry({tool.name: spec}).snapshot(), tool.name,
                                args, context or ToolCallContext())
