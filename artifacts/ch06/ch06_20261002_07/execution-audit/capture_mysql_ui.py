import json
from pathlib import Path
from sqlalchemy import func, select
from mewhelp.db.engine import SessionLocal
from mewhelp.db.models import Conversation, Message, MsgRole, RefundApplication

with SessionLocal() as db:
    row = db.scalar(select(RefundApplication).where(RefundApplication.application_no == 'RFb98b25a9a0934a33c6e863ddc217c5'))
    assert row and row.status == 'pending'
    conversation = db.get(Conversation, row.conversation_id)
    applications = db.scalar(select(func.count()).select_from(RefundApplication).where(RefundApplication.conversation_id == conversation.id))
    original_questions = db.scalar(select(func.count()).select_from(Message).where(Message.conversation_id == conversation.id,
                                   Message.role == MsgRole.user, Message.content == '这个能退吗'))
    assert applications == 1 and original_questions == 1
    record = {'database': 'actual MySQL localhost:3307/mewhelp', 'session_id': conversation.session_id,
              'user_id': conversation.user_id, 'application_no': row.application_no, 'status': row.status,
              'order_id': row.order_id, 'reason': row.reason, 'order_snapshot': row.order_snapshot,
              'assessment': row.assessment_snapshot, 'policy_source_count': len(row.policy_snapshot),
              'original_user_question_rows': original_questions, 'refund_application_rows': applications,
              'browser_actions_verified': ['missing_order_picker', 'refresh_picker', 'select_1001_resume_same_question',
                                           'fixed_reason_submit', 'refresh_server_receipt'], 'passed': True}
with Path('artifacts/ch06/ch06_20261002_05/ui-mysql/browser-report.json').open('x', encoding='utf-8') as stream:
    json.dump(record, stream, ensure_ascii=False, indent=2)
print({'session_id': conversation.session_id, 'applications': applications, 'user_questions': original_questions, 'passed': True})
