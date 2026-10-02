"""Single pure demo fact source; three owned templates plus legacy seeded reads."""

import random
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

AS_OF = date(2026, 10, 2)
DEMO_ORDER_IDS = ("1001", "1002", "1003")
PRODUCTS = ("轻量跑鞋", "降噪耳机", "机械键盘", "保温杯", "双肩包")
CARRIERS = ("顺丰速运", "中通快递", "圆通速递", "京东物流")
CITIES = ("杭州转运中心", "上海分拨中心", "广州集散中心", "北京顺义中转场")
ORDER_STATES = ("已下单", "已发货", "运输中", "已签收")


def seeded_rng(tool_name: str, key: str) -> random.Random:
    return random.Random(f"{tool_name}:{key}")


@dataclass(frozen=True)
class DemoOrderRecord:
    order_id: str
    product_name: str
    product_category: str
    status: str
    paid_amount: Decimal
    ordered_at: date
    received_at: date | None
    condition: Literal["unopened", "opened", "unknown"]
    as_of: date = AS_OF


_CATALOG = {
    "1001": DemoOrderRecord(
        "1001",
        "机械键盘",
        "数码配件",
        "已签收",
        Decimal("199.00"),
        date(2026, 9, 27),
        date(2026, 9, 29),
        "unopened",
    ),
    "1002": DemoOrderRecord(
        "1002",
        "无线耳机",
        "数码配件",
        "已签收",
        Decimal("399.00"),
        date(2026, 9, 10),
        date(2026, 9, 15),
        "unknown",
    ),
    "1003": DemoOrderRecord(
        "1003", "保温杯", "家居用品", "待发货", Decimal("89.00"), date(2026, 10, 1), None, "unknown"
    ),
}


def get_demo_order_record(order_id: str) -> DemoOrderRecord:
    if order_id in _CATALOG:
        return _CATALOG[order_id]
    # Kept only for the older read-only demonstration tools, never an owned selection.
    rng = seeded_rng("query_order", order_id)
    product = rng.choice(PRODUCTS)
    status = rng.choice(ORDER_STATES)
    amount = rng.randint(59, 899)
    ordered = date(2026, 9, 1) + timedelta(days=seeded_rng("order_date", order_id).randint(0, 20))
    return DemoOrderRecord(
        order_id, product, "演示商品", status, Decimal(amount), ordered, None, "unknown"
    )


def format_demo_order(order: DemoOrderRecord) -> str:
    received = f",签收时间 {order.received_at:%Y-%m-%d}" if order.received_at else ""
    return (
        f"订单 {order.order_id}:商品「{order.product_name}」,下单时间 {order.ordered_at:%Y-%m-%d},"
        f"实付 {order.paid_amount:.2f} 元,当前状态「{order.status}」{received}。"
    )


def format_demo_logistics(order: DemoOrderRecord) -> str:
    if order.order_id in DEMO_ORDER_IDS and order.status == "待发货":
        return f"订单 {order.order_id} 尚未发货，暂没有运单或运输轨迹。"
    rng = seeded_rng("query_logistics", order.order_id)
    carrier = rng.choice(CARRIERS)
    waybill = f"{rng.choice('SFYTJD')}{rng.randint(10**11, 10**12 - 1)}"
    nodes = rng.sample(CITIES, k=rng.randint(2, 4))
    legacy_latest = order.ordered_at + timedelta(days=rng.randint(1, 5))
    legacy_status = rng.choice(["运输中", "派送中", "已签收"])
    latest = order.received_at or legacy_latest
    status = order.status if order.order_id in DEMO_ORDER_IDS else legacy_status
    location = "已由收件人签收" if order.received_at else nodes[-1]
    return (
        f"订单 {order.order_id} 的物流:承运商 {carrier},运单号 {waybill},"
        f"当前状态「{status}」,最新位置 {location}({latest:%Y-%m-%d})。"
        f"轨迹:{' → '.join(nodes)}。"
    )
