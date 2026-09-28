"""三个 mock 工具 —— 订单 / 商品 / 物流。

**不接真实接口、不建表**:内部数据在工具里生成。这是需求写死的。
真实系统里这三个会去调公司的电商与物流 API,本章用不到。

**随机源由入参定种子**:`random.Random(f"{tool}:{arg}")`。同一个 order_id
永远给出同一份物流 —— 验收①可复现、可截图,评估集也能断言内容。
真实随机留给"换个订单号"这个维度,那才是用户能感知的变化。
"""

import random
from datetime import date, timedelta

from langchain_core.tools import BaseTool, tool

# 固定的"当前时间"基准。用 datetime.now() 的话同一入参在不同时刻给出不同结果,
# 与"可复现"直接冲突。
_BASE_DATE = "2026-09-01"

_PRODUCTS = ["轻量跑鞋", "降噪耳机", "机械键盘", "保温杯", "双肩包"]
_CARRIERS = ["顺丰速运", "中通快递", "圆通速递", "京东物流"]
_CITIES = ["杭州转运中心", "上海分拨中心", "广州集散中心", "北京顺义中转场"]
_ORDER_STATES = ["已下单", "已发货", "运输中", "已签收"]


def _rng(tool_name: str, key: str) -> random.Random:
    """按 (工具名, 入参) 起种子 —— 同入参必得同输出。"""
    return random.Random(f"{tool_name}:{key}")


def _order_date(order_id: str) -> date:
    """下单日 —— `query_order` 与 `query_logistics` **共用**这一条推导。

    两条工具各起各的种子时,物流会显示"09-03 已到达"而订单"09-11 才下单":
    单看一条都正常,同屏出现就成了"包裹比订单先到"。验收演示里用户问完物流
    接着问订单,这一幕正好会被看到。所以日期只留一个来源,而不是两处各推一遍。
    """
    days = _rng("order_date", order_id).randint(0, 20)
    return date.fromisoformat(_BASE_DATE) + timedelta(days=days)


def build_business_tools() -> list[BaseTool]:
    """返回三个 mock 工具。

    不收 session 工厂:这三个不碰数据库(需求写死),所以闭包捕获的东西是空集。
    签名这一点由 test_tool_business.py 断言着。
    """

    @tool
    def query_order(order_id: str) -> str:
        """按订单号查订单状态。order_id 是订单号,例如 1001。"""
        rng = _rng("query_order", order_id)
        product = rng.choice(_PRODUCTS)
        state = rng.choice(_ORDER_STATES)
        amount = rng.randint(59, 899)
        ordered = _order_date(order_id)
        return (
            f"订单 {order_id}:商品「{product}」,下单时间 {ordered:%Y-%m-%d},"
            f"实付 {amount} 元,当前状态「{state}」。"
        )

    @tool
    def query_product(product_name: str) -> str:
        """按商品名查价格与库存。product_name 是商品名称。"""
        rng = _rng("query_product", product_name)
        price = rng.randint(39, 1299)
        stock = rng.choice([0, rng.randint(1, 300)])
        spec = rng.choice(["标准版", "升级版", "礼盒装"])
        availability = "暂时缺货,可设置到货提醒" if stock == 0 else f"库存 {stock} 件"
        return f"商品「{product_name}」({spec}):售价 {price} 元,{availability}。"

    @tool
    def query_logistics(order_id: str) -> str:
        """按订单号查物流轨迹。order_id 是订单号,例如 1001。"""
        rng = _rng("query_logistics", order_id)
        carrier = rng.choice(_CARRIERS)
        waybill = f"{rng.choice('SFYTJD')}{rng.randint(10**11, 10**12 - 1)}"
        nodes = rng.sample(_CITIES, k=rng.randint(2, 4))
        timeline = " → ".join(nodes)
        # 从**下单日**往后推,而不是从固定基准日 —— 否则会推出早于下单的"已到达"。
        # 日期本身仍来自 _BASE_DATE,不用 datetime.now():可复现优先。
        latest = _order_date(order_id) + timedelta(days=rng.randint(1, 5))
        status = rng.choice(["运输中", "派送中", "已签收"])
        return (
            f"订单 {order_id} 的物流:承运商 {carrier},运单号 {waybill},"
            f"当前状态「{status}」,最新位置 {nodes[-1]}({latest:%Y-%m-%d})。"
            f"轨迹:{timeline}。"
        )

    return [query_order, query_product, query_logistics]
