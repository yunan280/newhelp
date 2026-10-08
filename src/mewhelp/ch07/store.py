from collections.abc import Sequence

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from mewhelp.db.models import Conversation, ConversationSummary, Message, MsgRole

from .config import BudgetProfile
from .tokens import estimate_text
from .types import ConversationSnapshot, LedgerMessage, SummaryJob, SummarySegment


def _segment(row: ConversationSummary) -> SummarySegment:
    return SummarySegment(row.id, row.seq, row.from_msg_id, row.upto_msg_id, row.content)


def latest_segments(segments: Sequence[SummarySegment], *, profile: BudgetProfile
                    ) -> tuple[SummarySegment, ...]:
    kept = []
    for segment in reversed(segments):
        candidate = [segment, *kept]
        if estimate_text('\n'.join(s.content for s in candidate), profile=profile) > profile.summary_reserve:
            break
        kept = candidate
    return tuple(kept)


def read_conversation(session: Session, *, conversation_id: int,
                      user_id: str) -> ConversationSnapshot:
    conv = session.scalar(select(Conversation).where(Conversation.id == conversation_id,
                                                     Conversation.user_id == user_id))
    if conv is None:
        raise LookupError('conversation not found')
    rows = session.scalars(select(Message).where(Message.conversation_id == conversation_id)
                            .order_by(Message.id)).all()
    visible = tuple(LedgerMessage(row.id, row.role.value, row.content or '',
                                  tuple(row.citations or []), row.ch06_event_key)
                    for row in rows if row.role is MsgRole.user or (
                        row.role is MsgRole.assistant and not row.tool_calls and row.content))
    segments = tuple(_segment(row) for row in session.scalars(select(ConversationSummary)
        .where(ConversationSummary.conversation_id == conversation_id)
        .order_by(ConversationSummary.seq)))
    return ConversationSnapshot(conv.id, conv.session_id, conv.user_id,
        conv.summary_upto_msg_id or 0, conv.layer1_from_msg_id or 0, visible, segments)


def advance_layer1(session: Session, *, conversation_id: int,
                   expected_layer1: int, new_layer1: int) -> bool:
    conv = session.scalar(select(Conversation).where(Conversation.id == conversation_id)
                           .with_for_update().execution_options(populate_existing=True))
    if conv is None or (conv.layer1_from_msg_id or 0) != expected_layer1 or new_layer1 < expected_layer1:
        return False
    end = session.get(Message, new_layer1)
    if end is None or end.conversation_id != conversation_id or end.role is not MsgRole.assistant:
        raise ValueError('layer1 boundary must end a conversation-local visible turn')
    claimed = session.execute(update(Conversation).where(Conversation.id == conversation_id,
        func.coalesce(Conversation.layer1_from_msg_id, 0) == expected_layer1)
        .values(layer1_from_msg_id=new_layer1).execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        return False
    session.expire(conv)
    session.flush()
    return True


def append_summary(session: Session, *, job: SummaryJob, from_msg_id: int,
                   upto_msg_id: int, content: str, profile: BudgetProfile) -> SummarySegment | None:
    conv = session.scalar(select(Conversation).where(Conversation.id == job.conversation_id)
                           .with_for_update().execution_options(populate_existing=True))
    if conv is None or (conv.summary_upto_msg_id or 0) != job.old_upto_msg_id:
        return None
    if not job.turns or (from_msg_id, upto_msg_id) != (
        job.turns[0].from_msg_id, job.turns[-1].upto_msg_id):
        raise ValueError('summary range differs from supplied batch')
    first = session.scalar(select(Message).where(Message.conversation_id == job.conversation_id,
        Message.id > job.old_upto_msg_id, Message.role == MsgRole.user).order_by(Message.id))
    end = session.get(Message, upto_msg_id)
    if (first is None or first.id != from_msg_id or end is None
        or end.conversation_id != job.conversation_id or end.role is not MsgRole.assistant
        or not from_msg_id <= upto_msg_id <= job.layer1_snapshot_id <= (conv.layer1_from_msg_id or 0)):
        raise ValueError('summary range must cover the next complete local batch')
    # Claim coverage atomically in the same transaction as the immutable row.
    # This also protects backends that ignore SELECT FOR UPDATE.
    claimed = session.execute(update(Conversation).where(Conversation.id == job.conversation_id,
        func.coalesce(Conversation.summary_upto_msg_id, 0) == job.old_upto_msg_id)
        .values(summary_upto_msg_id=upto_msg_id).execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        return None
    session.expire(conv)
    previous = tuple(_segment(row) for row in session.scalars(select(ConversationSummary)
        .where(ConversationSummary.conversation_id == job.conversation_id)
        .order_by(ConversationSummary.seq)))
    row = ConversationSummary(conversation_id=job.conversation_id,
        seq=previous[-1].seq + 1 if previous else 1, from_msg_id=from_msg_id,
        upto_msg_id=upto_msg_id, content=content)
    session.add(row)
    session.flush()
    segment = _segment(row)
    conv.summary = '\n'.join(s.content for s in latest_segments((*previous, segment), profile=profile))
    session.flush()
    return segment


def find_ledger_ids(session: Session, *, conversation_id: int,
                    event_keys: Sequence[str]) -> dict[str, int]:
    return dict(session.execute(select(Message.ch06_event_key, Message.id).where(
        Message.conversation_id == conversation_id, Message.ch06_event_key.in_(event_keys))).all())


def list_user_conversations(session: Session, *, user_id: str):
    from .schemas import ConversationItem
    first = select(Message.content).where(Message.conversation_id == Conversation.id,
        Message.role == MsgRole.user).order_by(Message.id).limit(1).correlate(Conversation).scalar_subquery()
    count = select(func.count(ConversationSummary.id)).where(
        ConversationSummary.conversation_id == Conversation.id).correlate(Conversation).scalar_subquery()
    rows = session.execute(select(Conversation, first, count).where(Conversation.user_id == user_id)
        .order_by(Conversation.created_at.desc(), Conversation.id.desc()))
    return [ConversationItem(id=c.id, session_id=c.session_id, created_at=c.created_at,
        updated_at=c.updated_at, first_question=(question or '')[:40],
        has_summary=bool(n), summary_count=n) for c, question, n in rows]


def read_visible_messages(session: Session, *, conversation_id: int, user_id: str):
    from .schemas import ConversationMessages, VisibleMessage
    snapshot = read_conversation(session, conversation_id=conversation_id, user_id=user_id)
    rows={r.id:r for r in session.scalars(select(Message).where(Message.conversation_id==conversation_id))}
    def feedback_fields(message):
        row=rows[message.id]
        saved=row.retrieval_snapshot or {}
        eligible=message.role=='assistant' and saved.get('answer_status','completed')=='completed'
        return {'answer_message_id':str(message.id) if eligible else None,
                'feedback_status':'down' if saved.get('feedback_lcq_id') else 'none'}
    return ConversationMessages(id=snapshot.conversation_id, session_id=snapshot.session_id,
        messages=[VisibleMessage(id=m.id, role=m.role, content=m.content, citations=list(m.citations),**feedback_fields(m))
                  for m in snapshot.messages])
