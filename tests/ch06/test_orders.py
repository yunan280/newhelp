from datetime import date
from decimal import Decimal
from importlib import import_module

import pytest

from mewhelp.tools.business import build_business_tools


def orders_module():
    try:
        return import_module("mewhelp.ch06.orders")
    except ImportError:
        pytest.fail("missing owned demo order catalog")


async def test_card_and_query_order_share_facts():
    order = orders_module().load_demo_order("alice", "1001")
    assert order.product_name == "机械键盘"
    assert order.paid_amount == Decimal("199.00")
    assert order.ordered_at == date(2026, 9, 27) and order.received_at == date(2026, 9, 29)
    assert order.condition == "unopened" and order.status == "已签收"
    tools = {tool.name: tool for tool in build_business_tools()}
    text = await tools["query_order"].ainvoke({"order_id": "1001"})
    assert all(value in text for value in ["机械键盘", "199.00", "2026-09-27", "已签收"])
    from mewhelp.ch08.mcp_servers.mock_data import logistics_data
    logistics = logistics_data('1001')['data']['description']
    assert "2026-09-29" in logistics and "已签收" in logistics


def test_unknown_order_needs_selection():
    orders = orders_module()
    assert "999999" not in {item.order_id for item in orders.list_demo_orders("alice")}
    with pytest.raises(orders.UnknownOrderError):
        orders.load_demo_order("alice", "999999")


def test_demo_owner_is_bound_to_request_identity():
    orders = orders_module()
    alice = orders.load_demo_order("alice", "1001")
    bob = orders.load_demo_order("bob", "1001")
    assert alice.user_id == "alice" and bob.user_id == "bob"
    assert all(item.user_id == "alice" for item in orders.list_demo_orders("alice"))
    assert alice is not bob
    with pytest.raises(ValueError):
        orders.list_demo_orders(" ")


async def test_unshipped_order_has_no_invented_delivery_or_condition():
    order = orders_module().load_demo_order("alice", "1003")
    assert order.status == "待发货" and order.received_at is None and order.condition == "unknown"
    assert order.as_of == date(2026, 10, 2)
    from mewhelp.ch08.mcp_servers.mock_data import logistics_data
    logistics = logistics_data('1003')['data']['description']
    assert "未发货" in logistics and "已签收" not in logistics and "运单号" not in logistics


def test_unknown_condition_remains_unknown():
    order = orders_module().load_demo_order("alice", "1002")
    assert order.condition == "unknown" and order.paid_amount == Decimal("399.00")
