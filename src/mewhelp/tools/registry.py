"""统一注册与原子快照。工具发现不授予外部工具权限。"""
import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import replace
from threading import RLock
from types import MappingProxyType

from langchain_core.tools import BaseTool

from .contracts import ToolCallContext, ToolResult, ToolSnapshot, ToolSpec
from .validation import normalize_schema


class ToolRegistry:
    def __init__(self, specs: Mapping[str, ToolSpec] | None = None, *, engine=None):
        self._lock = RLock()
        self._specs: dict[str, ToolSpec] = {}
        self._owners: dict[str, str] = {}
        self.engine = engine
        for name, spec in (specs or {}).items():
            if name != spec.tool.name:
                raise ValueError('注册名必须与工具名一致')
            self.register(spec)

    @staticmethod
    def _normalize(spec: ToolSpec) -> ToolSpec:
        if not spec.tool.name.strip() or not spec.tool.description.strip():
            raise ValueError('工具必须有名称和用途描述')
        if spec.source not in ('builtin', 'mcp') or spec.permission not in ('readonly', 'write', 'deny'):
            raise ValueError('工具来源或本地权限不合法')
        if spec.source == 'mcp' and not spec.mcp_server:
            raise ValueError('MCP 工具必须声明来源 Server')
        if spec.tool.name == 'create_ticket' and (spec.source != 'builtin' or spec.permission not in ('write', 'deny')):
            raise ValueError('create_ticket 是受保护的内置写工具')
        schema = normalize_schema(spec.tool, spec.input_schema, forbid_extra=True)
        if spec.timeout_seconds <= 0:
            raise ValueError('超时必须大于零')
        return replace(spec, input_schema=schema)

    def register(self, spec: ToolSpec) -> None:
        spec = self._normalize(spec)
        with self._lock:
            if spec.tool.name in self._specs:
                raise ValueError(f'工具重名: {spec.tool.name}')
            self._specs[spec.tool.name] = spec
            self._owners[spec.tool.name] = 'builtin'

    def replace_source(self, source: str, specs: Sequence[ToolSpec]) -> None:
        normalized = [self._normalize(s) for s in specs]
        names = [s.tool.name for s in normalized]
        if len(names) != len(set(names)):
            raise ValueError('同一来源工具重名')
        with self._lock:
            for name in names:
                if name == 'create_ticket' or (name in self._specs and self._owners[name] != source):
                    raise ValueError(f'来源不可覆盖工具: {name}')
            new = {n: s for n, s in self._specs.items() if self._owners[n] != source}
            owners = {n: o for n, o in self._owners.items() if o != source}
            new.update({s.tool.name: s for s in normalized})
            owners.update({n: source for n in names})
            self._specs, self._owners = new, owners

    def snapshot(self) -> ToolSnapshot:
        with self._lock:
            specs = {n: replace(s, input_schema=deepcopy(s.input_schema),
                                enum_labels=deepcopy(s.enum_labels)) for n, s in self._specs.items()}
        identity = [{'name': n, 'schema': s.input_schema, 'description': s.tool.description,
                     'source': s.source, 'server': s.mcp_server, 'permission': s.permission,
                     'available': s.available, 'visible': s.model_visible}
                    for n, s in sorted(specs.items())]
        digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        return ToolSnapshot(MappingProxyType(specs), digest)

    def set_builtin_ticket_permission(self, permission: str):
        if permission not in ('write', 'deny'):
            raise ValueError('建单本地权限只能 write/deny')
        with self._lock:
            spec = self._specs.get('create_ticket')
            if spec and self._owners.get('create_ticket') == 'builtin':
                self._specs['create_ticket'] = replace(spec, permission=permission)

    def names(self) -> list[str]:
        return list(self.snapshot().specs)

    def tools(self) -> list[BaseTool]:
        return self.snapshot().tools()

    def get(self, name: str) -> ToolSpec | None:
        return self.snapshot().get(name)

    async def run(self, name: str, args: dict, *, context: ToolCallContext | None = None) -> ToolResult:
        from .engine import ToolExecutionEngine
        engine = self.engine or ToolExecutionEngine()
        return await engine.execute(self.snapshot(), name, args, context or ToolCallContext())

    async def run_all(self, calls: Sequence[Mapping], *, context: ToolCallContext | None = None) -> list[ToolResult]:
        return list(await asyncio.gather(*(self.run(c['name'], dict(c['args']),
                    context=replace(context or ToolCallContext(), tool_call_id=c.get('id')))
                    for c in calls)))
