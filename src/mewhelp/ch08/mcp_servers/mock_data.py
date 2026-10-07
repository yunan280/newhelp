"""输入定种子的 mock 数据，与既有演示订单事实一致。"""
from datetime import timedelta

from mewhelp.tools.business_data import (
    CARRIERS, CITIES, get_demo_order_record, seeded_rng,
)


def logistics_data(order_id: str) -> dict:
    order = get_demo_order_record(order_id)
    rng = seeded_rng('mcp_logistics', order_id)
    status = 'UNSHIPPED' if order.status == '待发货' else 'SIGNED' if order.received_at else 'SHIPPING'
    data = {
        'order_id': order_id, 'status': status,
        'carrier': None if status == 'UNSHIPPED' else rng.choice(CARRIERS),
        'waybill': None if status == 'UNSHIPPED' else f'SF{rng.randint(10**11, 10**12 - 1)}',
        'latest_location': '已由收件人签收' if status == 'SIGNED' else None if status == 'UNSHIPPED' else rng.choice(CITIES),
        'latest_at': str(order.received_at or order.ordered_at + timedelta(days=1)),
        'tracking': [] if status == 'UNSHIPPED' else rng.sample(CITIES, 2),
    }
    labels = {'UNSHIPPED': '待发货', 'SIGNED': '已签收', 'SHIPPING': '运输中'}
    data['description'] = (f'订单 {order_id} 尚未发货，暂没有运单或运输轨迹。' if status == 'UNSHIPPED' else
        f"订单 {order_id} 的物流:承运商 {data['carrier']},运单号 {data['waybill']},"
        f"当前状态「{labels[status]}」,最新位置 {data['latest_location']}({data['latest_at']})。"
        f"轨迹:{' → '.join(data['tracking'])}。")
    return {'outcome': 'success', 'data': data, 'message': '物流 mock 查询完成'}


def warranty_data(order_id: str) -> dict:
    order = get_demo_order_record(order_id)
    rng = seeded_rng('mcp_warranty', order_id)
    return {'outcome': 'success', 'data': {'order_id': order_id, 'product_name': order.product_name,
        'status': rng.choice(['IN_WARRANTY', 'IN_WARRANTY', 'OUT_OF_WARRANTY']),
        'expires_at': str(order.ordered_at + timedelta(days=365))}, 'message': '在保 mock 查询完成'}


def return_data(return_id: str) -> dict:
    rng = seeded_rng('mcp_return', return_id)
    return {'outcome': 'success', 'data': {'return_id': return_id,
        'status': rng.choice(['APPLIED', 'RECEIVED', 'REFUNDED']),
        'updated_at': '2026-10-07', 'description': '退货进度演示数据'}, 'message': '退货进度 mock 查询完成'}
