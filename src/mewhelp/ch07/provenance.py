import re
from collections.abc import Sequence

from mewhelp.ch06.orders import UnknownOrderError, load_demo_order
from mewhelp.db.models import ConversationSummary

from .store import read_conversation
from .types import HistoryContext


def _order_ids(text):
    return set(re.findall(r'订单(?:号)?[：:为是\s]*([A-Za-z0-9-]+)', text))


def reference_sources(history: HistoryContext, *, user_id: str, session_factory) -> list[dict]:
    sources = []
    with session_factory() as session:
        try:
            snapshot = read_conversation(session, conversation_id=history.conversation_id, user_id=user_id)
        except LookupError:
            return []
        ledger = {m.id: m for m in snapshot.messages}
        def add(order_id, source_id, first, last):
            try:
                product = load_demo_order(user_id, order_id).product_name
            except UnknownOrderError:
                product = ''
            sources.append({'order_id': order_id, 'message_id': source_id,
                            'user_id': user_id, 'product_name': product,
                            'from_msg_id': first, 'last_msg_id': last})
        for segment in history.summary_segments:
            saved = session.get(ConversationSummary, segment.id)
            if (saved is None or saved.conversation_id != history.conversation_id
                or (saved.from_msg_id, saved.upto_msg_id, saved.content) != (
                    segment.from_msg_id, segment.upto_msg_id, segment.content)):
                continue
            originals = [m for m in snapshot.messages if segment.from_msg_id <= m.id <= segment.upto_msg_id]
            # Summary text locates an object, while originals prove it was supplied
            # in this conversation. It grants no authority about current status.
            explicit = set().union(*(_order_ids(m.content) for m in originals if m.role == 'user'))
            for order_id in sorted(explicit & _order_ids(segment.content)):
                mentions = [m.id for m in originals if m.role == 'user' and order_id in _order_ids(m.content)]
                add(order_id, f'summary-{segment.id}', min(mentions), max(mentions))
        for message in (*history.layer2, *history.layer1):
            meta = message.additional_kwargs.get('ch07', {})
            ident = meta.get('ledger_id')
            if not ident and message.id and message.id.startswith('mysql-'):
                ident = int(message.id[6:])
            original = ledger.get(ident)
            if original is None or original.role != 'user':
                continue
            for order_id in sorted(_order_ids(original.content)):
                add(order_id, message.id or f'mysql-{ident}', original.id, original.id)
    return sources


def verify_reference(*, order_id: str, source_id: str, sources: Sequence[dict]) -> bool:
    return any(s['order_id'] == order_id and s['message_id'] == source_id for s in sources)


def chronological_reference(question: str, sources: Sequence[dict]) -> str | None:
    positions = {}
    latest = {}
    for source in sources:
        if isinstance(source.get('from_msg_id'), int):
            order = source['order_id']
            positions[order] = min(positions.get(order, source['from_msg_id']), source['from_msg_id'])
            last = source.get('last_msg_id', source['from_msg_id'])
            latest[order] = max(latest.get(order, last), last)
    if not positions:
        return None
    ranked = sorted(positions, key=positions.get)
    positive = re.sub(r'(?:不是|不要查|别查)\s*第一(?:个订单|笔订单|单)?', '', question)
    if re.search(r'最开始|最早|最初|第一(?:个订单|笔订单|单)', positive):
        return ranked[0] if sum(p == positions[ranked[0]] for p in positions.values()) == 1 else None
    if re.search(r'第二(?:个订单|笔订单|单)', positive) and len(ranked) >= 2:
        return ranked[1] if len(set(positions.values())) == len(positions) else None
    if re.search(r'刚才|上次|上一(?:单|笔)|最近|最后|后一(?:单|笔)', positive):
        recent = [order for order, last in latest.items() if last == max(latest.values())]
        return recent[0] if len(recent) == 1 else None
    return None
