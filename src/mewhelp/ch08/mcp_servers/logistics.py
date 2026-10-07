"""python -m mewhelp.ch08.mcp_servers.logistics --port 9021"""
import argparse
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .mock_data import logistics_data


def build_server(*, host: str = '127.0.0.1', port: int = 9021) -> FastMCP:
    server = FastMCP('MewHelp Logistics', host=host, port=port, stateless_http=True, json_response=True)
    @server.tool()
    def query_logistics(order_id: Annotated[str, Field(min_length=1)]) -> dict:
        """按订单号查询物流轨迹、承运商、运单号和签收状态，仅返回 mock。"""
        return logistics_data(order_id)
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=9021)
    args = parser.parse_args()
    build_server(host=args.host, port=args.port).run(transport='streamable-http')


if __name__ == '__main__':
    main()
