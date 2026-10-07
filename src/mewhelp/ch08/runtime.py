"""共享工具生命周期：每轮刷新目录与本地权限，再冻结调用快照。"""
import asyncio
from dataclasses import replace

from langchain_mcp_adapters.client import MultiServerMCPClient

from mewhelp.tools.contracts import ToolSnapshot
from mewhelp.tools.plugins import PluginLoader
from mewhelp.tools.registry import ToolRegistry

from .config import ToolSystemSettings, read_tool_config
from .mcp_client import MCPToolProvider


class Ch08ToolRuntime:
    def __init__(self, registry: ToolRegistry, engine, settings: ToolSystemSettings, session_factory):
        self.registry, self.engine, self.settings = registry, engine, settings
        registry.engine = engine
        self.session_factory = session_factory
        self.plugins = PluginLoader(settings.plugin_dir)
        self.provider = None
        self._connections = None
        self._known: dict[str, list] = {}
        self._lock = asyncio.Lock()
        self.last_refresh = {}

    async def refresh(self) -> ToolSnapshot:
        async with self._lock:
            report = {'plugins': self.plugins.refresh(self.registry), 'servers': {}, 'config_error': None}
            try:
                config = read_tool_config(self.settings.config_path)
            except ValueError as exc:
                self.registry.set_builtin_ticket_permission('deny')
                report['config_error'] = str(exc)
                for server, specs in self._known.items():
                    self.registry.replace_source(f'mcp:{server}', [replace(s, permission='deny') for s in specs])
                self.last_refresh = report
                return self.registry.snapshot()
            connections = config['servers']
            self.registry.set_builtin_ticket_permission(config.get('builtin_permissions', {}).get('create_ticket', 'write'))
            if connections != self._connections:
                self.provider = MCPToolProvider(MultiServerMCPClient(connections, handle_tool_errors=False), discovery_timeout_seconds=config.get('discovery_timeout_seconds', 3.0))
                self._connections = connections
            self.provider.discovery_timeout_seconds = config.get('discovery_timeout_seconds', 3.0)
            for removed in set(self._known) - set(connections):
                self.registry.replace_source(f'mcp:{removed}', [])
                del self._known[removed]
            servers = list(connections)
            results = await asyncio.gather(*(self.provider.discover(s) for s in servers), return_exceptions=True)
            for server, discovered in zip(servers, results, strict=True):
                if isinstance(discovered, BaseException):
                    specs = [replace(s, available=False, permission='deny') for s in self._known.get(server, [])]
                    report['servers'][server] = f'{type(discovered).__name__}: {discovered}'
                else:
                    rules = config['permissions'].get(server, {})
                    specs = []
                    for spec in discovered:
                        rule = rules.get(spec.remote_name, {})
                        # External writes are outside this chapter even when local config says write.
                        permission = 'readonly' if rule.get('mode') == 'readonly' else 'deny'
                        specs.append(replace(spec, permission=permission,
                            timeout_seconds=config.get('execution_timeout_seconds', 3.0),
                            result_fields=tuple(rule['result_fields']) if 'result_fields' in rule else None,
                            enum_labels=rule.get('enum_labels', {})))
                    report['servers'][server] = {'discovered': len(specs)}
                try:
                    self.registry.replace_source(f'mcp:{server}', specs)
                    self._known[server] = specs
                except ValueError as exc:
                    self.registry.replace_source(f'mcp:{server}', [])
                    self._known[server] = []
                    report['servers'][server] = str(exc)
            self.last_refresh = report
            return self.registry.snapshot()

    async def aclose(self) -> None:
        # Adapters creates/closes sessions per discovery/call; no persistent client session.
        self.provider = None
        if self.engine.audit is not None:
            self.engine.audit.close()


def create_tool_runtime(session_factory, *, settings: ToolSystemSettings | None = None):
    """启动登记内置工具，handler 每次调用再绑定可信上下文。"""
    from mewhelp.tools.audit import ToolAuditWriter
    from mewhelp.tools.business import build_business_tools
    from mewhelp.tools.contracts import ToolSpec
    from mewhelp.tools.engine import ToolExecutionEngine
    from mewhelp.tools.knowledge import build_knowledge_tools
    from mewhelp.tools.ticket import build_ticket_spec
    from mewhelp.tools.workflow import build_load_order_spec
    engine = ToolExecutionEngine(ToolAuditWriter(session_factory))
    registry = ToolRegistry(engine=engine)
    for tool in build_business_tools():
        registry.register(ToolSpec(tool))
    template = build_knowledge_tools(session_factory)[0]
    registry.register(ToolSpec(template, retryable=False, timeout_seconds=120,
        preserve_raw=True, tool_factory=lambda context: build_knowledge_tools(session_factory)[0]))
    registry.register(build_ticket_spec(session_factory))
    registry.register(build_load_order_spec())
    return Ch08ToolRuntime(registry, engine, settings or ToolSystemSettings(), session_factory)
