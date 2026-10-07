"""Old Ch02 protocol tests use an explicit offline MCP handler, never a builtin fallback."""
import pytest


@pytest.fixture(autouse=True)
def isolated_ch02_mcp(request, monkeypatch, tmp_path):
    if request.path.name not in {
        'test_ch02_service_prepare.py', 'test_ch02_service_run.py', 'test_ch02_service_stream.py',
        'test_ch02_api_agent.py', 'test_ch02_api_chat.py',
    }:
        yield
        return
    from langchain_core.tools import tool

    from mewhelp.ch02 import service
    from mewhelp.ch08.config import ToolSystemSettings
    from mewhelp.ch08.mcp_client import MCPToolProvider
    from mewhelp.ch08.mcp_servers.mock_data import logistics_data
    from mewhelp.ch08.runtime import create_tool_runtime
    from mewhelp.tools.contracts import ToolSpec
    @tool
    def query_logistics(order_id: str) -> str:
        """物流 MCP mock 的离线协议测试处理器。"""
        return logistics_data(order_id)['data']['description']
    spec = ToolSpec(query_logistics, source='mcp', mcp_server='logistics', remote_name='query_logistics')
    async def discover(self, server):
        assert server == 'logistics'
        return [spec]
    monkeypatch.setattr(MCPToolProvider, 'discover', discover)
    original = service.build_registry
    def build_registry(*args, **kwargs):
        registry = original(*args, **kwargs)
        registry.register(spec)
        return registry
    monkeypatch.setattr(service, 'build_registry', build_registry)
    factory = request.getfixturevalue('session_factory')
    config = tmp_path / 'offline-mcp.json'
    config.write_text('{"servers":{"logistics":{"url":"http://127.0.0.1:1/mcp","transport":"streamable_http"}},"permissions":{"logistics":{"query_logistics":{"mode":"readonly"}}}}')
    runtime = create_tool_runtime(factory, settings=ToolSystemSettings(config, tmp_path/'plugins'))
    # get_tool_runtime reads app.state, so supply its usual creation seam.
    import mewhelp.ch08.runtime as runtime_module
    monkeypatch.setattr(runtime_module, 'create_tool_runtime', lambda *args, **kwargs:runtime)
    yield
    runtime.engine.audit.close()
