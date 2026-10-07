"""工具系统跨入口共享契约；可执行对象只驻留进程内。"""
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal

from langchain_core.tools import BaseTool


@dataclass(frozen=True)
class ToolResult:
    name: str
    args: dict
    ok: bool
    content: str
    error: str | None
    elapsed_ms: int
    attempts: int
    artifact: object | None = None
    status: str = ''
    retry_count: int = 0
    source: Literal['builtin', 'mcp'] = 'builtin'
    mcp_server: str | None = None
    tool_call_id: str | None = None


@dataclass(frozen=True)
class WriteAuthorization:
    method: Literal['preview', 'legacy_button']
    confirmation_id: str
    conversation_id: int
    session_id: str
    user_id: str
    tool_name: str
    args_hash: str


@dataclass(frozen=True)
class ToolCallContext:
    conversation_id: int | None = None
    session_id: str | None = None
    user_id: str | None = None
    turn_id: str | None = None
    tool_call_id: str | None = None
    deadline_monotonic: float | None = None
    intent_evidence: dict | None = None
    authorization: WriteAuthorization | None = None


@dataclass(frozen=True)
class PreparedToolCall:
    name: str
    args: dict
    tool_call_id: str
    args_hash: str
    schema_hash: str
    requires_confirmation: bool
    result: ToolResult | None = None


@dataclass(frozen=True)
class ToolSpec:
    tool: BaseTool
    retryable: bool = True
    timeout_seconds: float = 3.0
    preserve_raw: bool = False
    source: Literal['builtin', 'mcp'] = 'builtin'
    mcp_server: str | None = None
    remote_name: str | None = None
    permission: Literal['readonly', 'write', 'deny'] = 'readonly'
    input_schema: dict | None = None
    tool_factory: Callable[[ToolCallContext], BaseTool] | None = None
    result_fields: tuple[str, ...] | None = None
    enum_labels: Mapping[str, str] | None = None
    model_visible: bool = True
    available: bool = True


@dataclass(frozen=True)
class ToolSnapshot:
    specs: Mapping[str, ToolSpec]
    fingerprint: str

    def get(self, name: str) -> ToolSpec | None:
        return self.specs.get(name)

    def tools(self) -> list[BaseTool]:
        from copy import deepcopy
        return [s.tool.model_copy(update={'args_schema': deepcopy(s.input_schema)}) for s in self.specs.values()
                if s.model_visible and s.available and s.permission != 'deny']

    def catalog(self) -> list[dict]:
        from copy import deepcopy
        return [{'name': s.tool.name, 'description': s.tool.description,
                 'parameters': deepcopy(s.input_schema), 'source': s.source,
                 'mcp_server': s.mcp_server}
                for s in self.specs.values()
                if s.model_visible and s.available and s.permission != 'deny']
