"""按 Server 新建发现会话；返回未授权 ToolSpec 等待本地规则。"""
import asyncio

from langchain_mcp_adapters.client import MultiServerMCPClient

from mewhelp.tools.contracts import ToolSpec


class MCPToolProvider:
    def __init__(self, client: MultiServerMCPClient, *, discovery_timeout_seconds: float = 3.0):
        self.client = client
        self.discovery_timeout_seconds = discovery_timeout_seconds

    async def discover(self, server_name: str) -> list[ToolSpec]:
        tools = await asyncio.wait_for(self.client.get_tools(server_name=server_name), self.discovery_timeout_seconds)
        return [ToolSpec(tool, source='mcp', mcp_server=server_name,
                         remote_name=tool.name, permission='deny') for tool in tools]
