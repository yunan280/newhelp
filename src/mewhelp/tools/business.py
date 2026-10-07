"""内置订单、商品纯演示读工具，物流由独立 MCP 接管。

目录订单复用 business_data 的固定事实，与聊天订单卡片一致。
目录外旧订单与商品读工具继续按入参定种子，保证同一输入可复现。
"""

from langchain_core.tools import BaseTool, tool

from .business_data import (
    format_demo_order,
    get_demo_order_record,
    seeded_rng,
)


def build_business_tools() -> list[BaseTool]:
    """返回两个内置 mock 工具。

    不收 session 工厂:工具不碰数据库，所以闭包捕获的东西是空集。
    签名这一点由 test_tool_business.py 断言着。
    """

    @tool
    def query_order(order_id: str) -> str:
        """按订单号查订单状态。order_id 是订单号,例如 1001。"""
        return format_demo_order(get_demo_order_record(order_id))

    @tool
    def query_product(product_name: str) -> str:
        """按商品名查价格与库存。product_name 是商品名称。"""
        rng = seeded_rng("query_product", product_name)
        price = rng.randint(39, 1299)
        stock = rng.choice([0, rng.randint(1, 300)])
        spec = rng.choice(["标准版", "升级版", "礼盒装"])
        availability = "暂时缺货,可设置到货提醒" if stock == 0 else f"库存 {stock} 件"
        return f"商品「{product_name}」({spec}):售价 {price} 元,{availability}。"

    return [query_order, query_product]
