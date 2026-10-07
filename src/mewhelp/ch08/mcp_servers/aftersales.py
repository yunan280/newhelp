"""python -m mewhelp.ch08.mcp_servers.aftersales --port 9022"""
import argparse
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .mock_data import return_data, warranty_data


def build_server(*, host: str = '127.0.0.1', port: int = 9022) -> FastMCP:
    server = FastMCP('MewHelp Aftersales', host=host, port=port, stateless_http=True, json_response=True)
    @server.tool()
    def query_warranty(order_id: Annotated[str, Field(min_length=1)]) -> dict:
        """按订单号查询商品在保情况与保修到期日，仅返回 mock。"""
        return warranty_data(order_id)
    @server.tool()
    def query_return_progress(return_id: Annotated[str, Field(min_length=1)]) -> dict:
        """按退货单号查询退货入库及退款进度，仅返回 mock，不办理退货。"""
        return return_data(return_id)
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=9022)
    args = parser.parse_args()
    build_server(host=args.host, port=args.port).run(transport='streamable-http')


if __name__ == '__main__':
    main()
