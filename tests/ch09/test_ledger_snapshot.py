from dataclasses import replace

import pytest
from sqlalchemy import func, select

from mewhelp.db.models import Message, MsgRole
from mewhelp.db.repository import TurnMessage, append_messages_once, get_or_create_conversation


def test_feedback_mutation_does_not_break_ledger_replay(ch09_db, ch09_module):
    ch09_module('snapshots')
    assert 'retrieval_snapshot' in Message.__table__.columns
    snapshot = {'schema_version': 1, 'turn_id': 'turn', 'source_user_event_key': 'user-key',
                'source_user_message_id': '1', 'intent': '闲聊', 'retrieval_performed': False,
                'retrieved_chunks': None, 'answer_status': 'completed', 'trace_id': 'first'}
    row = TurnMessage(MsgRole.assistant, content='你好', ch06_event_key='answer-key',
                      retrieval_snapshot=snapshot)
    with ch09_db() as session:
        conv, _ = get_or_create_conversation(session, session_id='s', user_id='u')
        cid = conv.id
        append_messages_once(session, conversation_id=cid, rows=[row])
        original = session.scalar(select(Message))
        original_id = original.id
        original.retrieval_snapshot = {**snapshot, 'feedback_lcq_id': '9'}
        session.commit()
    with ch09_db() as session:
        append_messages_once(session, conversation_id=cid,
                             rows=[replace(row, retrieval_snapshot={**snapshot, 'trace_id': 'replay'})])
        session.commit()
        replayed = session.scalar(select(Message))
        assert replayed.id == original_id
        assert replayed.retrieval_snapshot['feedback_lcq_id'] == '9'
        assert replayed.retrieval_snapshot['trace_id'] == 'first'
        assert session.scalar(select(func.count(Message.id))) == 1
    with ch09_db() as session, pytest.raises(ValueError, match='different content'):
        append_messages_once(session, conversation_id=cid,
                             rows=[replace(row, retrieval_snapshot={**snapshot,
                                                                    'retrieved_chunks': {'text': '改原文'}})])


def test_refusal_snapshot_commits_independently(ch09_module, ch09_db):
    ch09_module('snapshots')
    from mewhelp.knowledge.refusals import LowConfidenceQuestion, RefusalInput, record_refusal
    snapshot = {'schema_version': 1, 'state': 'empty', 'chunks': [], 'query': '问',
                'filters': {}, 'top_k': 5, 'confidence': None}
    row_id = record_refusal(ch09_db, RefusalInput('问', None, 'cli', 'retrieval', 'no_evidence',
                                                 '未召回', retrieved_chunks=snapshot))
    with ch09_db() as session:
        assert session.get(LowConfidenceQuestion, int(row_id)).retrieved_chunks == snapshot
