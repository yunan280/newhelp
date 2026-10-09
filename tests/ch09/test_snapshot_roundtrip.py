from copy import deepcopy

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import sessionmaker

from mewhelp.db.models import Message, MsgRole
from mewhelp.db.repository import TurnMessage, append_messages_once


def snapshot():
    return {'turn_id': 'turn', 'retrieved_chunks': {'chunks': [
        {'text': '原文不能改', 'relevance_score': 0.9987983703613281}],
        'confidence': {'gap': 0.9972852260107175}}, 'retrieval_performed': True}


def test_mysql_double_text_roundtrip_keeps_snapshot_identity():
    from mewhelp.ch09.snapshots import same_message_snapshot
    original = snapshot()
    returned = deepcopy(original)
    returned['retrieved_chunks']['chunks'][0]['relevance_score'] = 0.998798370361328
    returned['retrieved_chunks']['confidence']['gap'] = 0.9972852260107176
    assert same_message_snapshot(original, returned)
    for changed in ('score', 'text', 'bool'):
        altered = deepcopy(returned)
        if changed == 'score':
            altered['retrieved_chunks']['chunks'][0]['relevance_score'] += .00001
        elif changed == 'text':
            altered['retrieved_chunks']['chunks'][0]['text'] = '替换原文'
        else:
            altered['retrieval_performed'] = 1
        assert not same_message_snapshot(original, altered)


@pytest.mark.mysql
def test_real_mysql_score_snapshot_commits_and_replays(ch09_mysql):
    factory = sessionmaker(ch09_mysql, expire_on_commit=False)
    row = TurnMessage(MsgRole.assistant, content='有证据的回答',
        ch06_event_key='score-answer', retrieval_snapshot=snapshot())
    with factory.begin() as db:
        db.execute(text("INSERT INTO conversations(id,session_id,user_id) VALUES(1,'scores','u')"))
        cid = 1
        append_messages_once(db, conversation_id=cid, rows=[row])
    with factory.begin() as db:
        append_messages_once(db, conversation_id=cid, rows=[row])
        assert len(db.scalars(select(Message)).all()) == 1
