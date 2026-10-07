from langchain_core.tools import tool

from mewhelp.ch05.intent import route_intent
from mewhelp.tools.contracts import ToolSpec
from mewhelp.tools.registry import ToolRegistry


def test_dynamic_fact_tool_routes_business_but_unknown_does_not():
    @tool
    def query_warranty(order_id: str) -> str:
        """查在保。"""
        return order_id
    snapshot = ToolRegistry({'query_warranty': ToolSpec(query_warranty)}).snapshot()
    assert route_intent('售后', 'order_specific', matched_tool='query_warranty', snapshot=snapshot) == 'business'
    assert route_intent('其他', matched_tool='invented', snapshot=snapshot) == 'other'
    assert route_intent('退款退货', 'order_specific', snapshot=snapshot) == 'aftersales'
    assert route_intent('退款退货', snapshot=snapshot) == 'knowledge'
    assert route_intent('投诉', snapshot=snapshot) == 'complaint'
