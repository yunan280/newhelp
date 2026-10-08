import pytest
from test_generation_boundary import state

from mewhelp.ch05 import workflow
from mewhelp.ch05.service import result_from_state
from mewhelp.ch09.contracts import MessageSnapshot
from mewhelp.db.models import Message


def ready_state(cid):
    return state(cid) | {'resumed':False, 'answer':'答复[1]。', 'refused':False, 'actions':[],
        'tool_trace':[], 'node_trace':[], 'messages':[], 'stop_reason':'completed',
        'retrieval_performed': True, 'retrieved_chunks': state(cid)['evidence']['retrieved_chunks'],
        'retrieval_events':[{'kind':'knowledge'}], 'trace_id':'trace-one'}


@pytest.mark.asyncio
async def test_committed_answer_contains_exact_source_identity_and_feedback_id(generation_context):
    ctx, cid = generation_context
    current = ready_state(cid)
    result = await workflow.log_node(current, ctx, lambda x: None)
    assert result['answer_message_id'].isdigit()
    with ctx.session_factory() as db:
        row = db.get(Message, int(result['answer_message_id']))
        snapshot = MessageSnapshot.model_validate(row.retrieval_snapshot)
        user = db.get(Message, int(snapshot.source_user_message_id))
        assert user.content == current['question']
        assert snapshot.source_user_event_key == user.ch06_event_key
        assert snapshot.retrieved_chunks.chunks[0].relevance_score == .9
    repeated = await workflow.log_node(current | {'trace_id':'replay'}, ctx, lambda x: None)
    assert repeated['answer_message_id'] == result['answer_message_id']


@pytest.mark.asyncio
async def test_failed_ledger_never_has_feedback_answer_id(generation_context):
    ctx, cid = generation_context
    ctx.session_factory = lambda: (_ for _ in ()).throw(RuntimeError('db failed'))
    result = await workflow.log_node(ready_state(cid), ctx, lambda x: None)
    assert result['ledger_error'] and result['answer_message_id'] is None


def test_waiting_result_never_inherits_old_feedback_id():
    value = ready_state(1) | {'status':'waiting_for_ticket', 'answer_message_id':'123'}
    assert result_from_state(value).answer_message_id is None


@pytest.mark.parametrize('phase',['waiting','waiting_ticket'])
def test_both_preview_ledgers_remain_ineligible_for_feedback(generation_context, phase):
    ctx, cid = generation_context
    ids = workflow._write_ledger(ctx, ready_state(cid), phase=phase)
    with ctx.session_factory() as db:
        row = db.get(Message, ids['answer'])
        assert row.retrieval_snapshot['answer_status'] == 'waiting'
