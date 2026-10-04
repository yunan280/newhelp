import datetime as dt

import httpx
import pytest
from sqlalchemy import select

from mewhelp.db.models import Conversation, ConversationSummary, Message, MsgRole


@pytest.fixture
def api(workflow_runtime, session_factory):
    from mewhelp.ch05.api import get_workflow_runtime
    from mewhelp.main import app
    app.dependency_overrides[get_workflow_runtime] = lambda: workflow_runtime
    with session_factory.begin() as session:
        session.add_all([Conversation(id=1, session_id='old', user_id='alice',
            created_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC), summary='梗概', summary_upto_msg_id=12, layer1_from_msg_id=12),
            Conversation(id=2, session_id='new', user_id='alice', created_at=dt.datetime(2026, 1, 2, tzinfo=dt.UTC),
                layer1_from_msg_id=22),
            Conversation(id=3, session_id='foreign', user_id='bob', created_at=dt.datetime(2026, 1, 3, tzinfo=dt.UTC))])
        session.flush()
        session.add_all([Message(id=10, conversation_id=1, role=MsgRole.user, content='首问原文' * 30),
            Message(id=11, conversation_id=1, role=MsgRole.tool, content='旧工具内容'),
            Message(id=12, conversation_id=1, role=MsgRole.assistant, content='答复全文' * 60,
                citations=[{'number': 1, 'answer': '完整证据'}]),
            Message(id=20, conversation_id=2, role=MsgRole.user, content='新会话首问'),
            Message(id=22, conversation_id=2, role=MsgRole.assistant, content='新答复'),
            ConversationSummary(conversation_id=1, seq=1, from_msg_id=10, upto_msg_id=12, content='梗概')])
    yield app
    app.dependency_overrides.pop(get_workflow_runtime, None)


async def test_list_newest_first_first_question_preview(api):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test') as client:
        response = await client.get('/api/conversations', params={'user_id': 'alice'})
    assert response.status_code == 200
    rows = response.json()['conversations']
    assert [r['session_id'] for r in rows] == ['new', 'old']
    assert rows[1]['first_question'] == ('首问原文' * 30)[:40]
    assert (rows[0]['has_summary'], rows[0]['summary_count']) == (False, 0)
    assert (rows[1]['has_summary'], rows[1]['summary_count']) == (True, 1)


async def test_messages_remain_original_after_summary(api):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test') as client:
        response = await client.get('/api/conversations/1/messages', params={'user_id': 'alice'})
    assert response.status_code == 200
    rows = response.json()['messages']
    assert [r['id'] for r in rows] == [10, 12]
    assert rows[0]['content'] == '首问原文' * 30 and rows[1]['content'] == '答复全文' * 60
    assert rows[1]['citations'][0]['answer'] == '完整证据'


async def test_cross_user_and_unknown_return_same_404(api):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test') as client:
        foreign = await client.get('/api/conversations/3/messages?user_id=alice')
        missing = await client.get('/api/conversations/999/messages?user_id=alice')
    assert foreign.status_code == missing.status_code == 404 and foreign.json() == missing.json()
    assert foreign.json()['detail'] == '会话不存在'


async def test_get_has_no_db_or_summary_side_effects(api, session_factory, workflow_runtime):
    def snapshot():
        with session_factory() as session:
            return [(r.id, r.summary, r.summary_upto_msg_id, r.layer1_from_msg_id) for r in
                session.scalars(select(Conversation).order_by(Conversation.id))], \
                [(r.id, r.content) for r in session.scalars(select(Message).order_by(Message.id))], \
                [(r.id, r.content) for r in session.scalars(select(ConversationSummary))]
    before = snapshot()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url='http://test') as client:
        assert (await client.get('/api/conversations?user_id=alice')).status_code == 200
        assert (await client.get('/api/conversations/1/messages?user_id=alice')).status_code == 200
        assert (await client.get('/api/conversations?user_id=%20%20')).status_code == 422
        assert (await client.get('/api/conversations')).json() == {'conversations': []}
    assert snapshot() == before and not workflow_runtime.context.summary_manager.inflight
