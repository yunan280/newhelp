"""固定流程工具可执行但不向模型开放，身份由可信上下文注入。"""
from langchain_core.tools import tool

from .contracts import ToolSpec


def build_load_order_spec():
    from mewhelp.ch06.orders import load_demo_order
    def factory(context):
        @tool
        def load_order(order_id: str) -> dict:
            """读取当前用户可选择的订单详情，供售后固定流程使用。"""
            return load_demo_order(context.user_id, order_id).model_dump(mode='json')
        return load_order
    return ToolSpec(factory(None), model_visible=False, retryable=False, tool_factory=factory)
