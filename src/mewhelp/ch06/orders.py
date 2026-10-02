from dataclasses import asdict

from mewhelp.ch05.schemas import OrderDTO
from mewhelp.tools.business_data import DEMO_ORDER_IDS, get_demo_order_record


class UnknownOrderError(ValueError):
    """This ID is not a selectable owned demo order; the workflow must offer selection."""


def load_demo_order(user_id: str, order_id: str) -> OrderDTO:
    if not user_id or not user_id.strip():
        raise ValueError("demo identity is required")
    if order_id not in DEMO_ORDER_IDS:
        raise UnknownOrderError("order is outside the current user's demo catalog")
    return OrderDTO(**asdict(get_demo_order_record(order_id)), user_id=user_id)


def list_demo_orders(user_id: str) -> list[OrderDTO]:
    return [load_demo_order(user_id, order_id) for order_id in DEMO_ORDER_IDS]
