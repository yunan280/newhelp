import pytest
from sqlalchemy import select
from test_generation_boundary import state

from mewhelp.ch05 import workflow
from mewhelp.ch09.confidence import ConfidenceProfile
from mewhelp.knowledge.refusals import LowConfidenceQuestion


def profile(threshold):
    return ConfidenceProfile(5, .6, (.5, .25, .25), threshold, {'revision':'r'},
                             {'corpus_hash':'a'*64, 'query_hash':'b'*64}, 'c'*64)


@pytest.mark.asyncio
async def test_formal_gate_refusal_preserves_round_evidence(generation_context):
    ctx, cid = generation_context
    ctx.confidence_profile = profile(.95)
    current = state(cid) | {'messages': [], 'tool_trace': [], 'actions': []}
    result = await workflow.gate_node(current, ctx, lambda x: None)
    assert not result['gate']['passed'] and result['refused']
    with ctx.session_factory() as db:
        row = db.scalar(select(LowConfidenceQuestion))
        assert row.retrieved_chunks['chunks'][0]['relevance_score'] == .9
        assert row.retrieved_chunks['confidence']['passed'] is False


@pytest.mark.asyncio
async def test_formal_gate_does_not_reuse_old_threshold(generation_context):
    ctx, cid = generation_context
    ctx.confidence_profile = profile(.4)
    current = state(cid) | {'messages': [], 'tool_trace': [], 'actions': []}
    result = await workflow.gate_node(current, ctx, lambda x: None)
    assert result['gate']['passed']
    assert result['gate']['evidence_confidence']['value'] == pytest.approx(.5)


@pytest.mark.asyncio
async def test_next_turn_clears_previous_retrieval_and_feedback_identity(generation_context):
    ctx, cid = generation_context
    current = state(cid) | {'retrieval_performed': True, 'retrieval_events': [{'old':'evidence'}],
                            'retrieved_chunks': {'old':True}, 'answer_message_id': '99'}
    result = await workflow.begin_turn(current, ctx, lambda x: None)
    assert result['retrieval_performed'] is False
    assert result['retrieval_events'] == [] and result['retrieved_chunks'] is None
    assert result['answer_message_id'] is None


@pytest.mark.asyncio
async def test_topk_drift_is_rejected_at_startup(tmp_path):
    from mewhelp.ch05.config import Ch05Settings
    from mewhelp.ch05.runtime import open_runtime
    from mewhelp.ch07.config import BudgetProfile, ContextSettings
    with pytest.raises(ValueError, match='TopK'):
        async with open_runtime(lambda: None, settings=Ch05Settings(checkpoint_path=tmp_path/'checkpoint'),
            context_settings=ContextSettings(rerank_top_k=3), context_profile=BudgetProfile(),
            confidence_profile=profile(.4)):
            pytest.fail('mismatched TopK starts')


@pytest.mark.asyncio
async def test_actual_graph_rejects_before_agent_and_persists_snapshot(generation_context, monkeypatch, tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from mewhelp.ch05.evidence import EvidenceEnvelope

    ctx, cid = generation_context
    ctx.confidence_profile = profile(.95)
    ctx.rag_factory = lambda: None
    current = state(cid)
    async def understand(s, c, e):
        return {'resolved_question':s['question']}
    async def classify(s, c, e):
        return {'intent':'商品咨询','scope':'general'}
    async def retrieve(*a, **kw):
        return EvidenceEnvelope.model_validate(current['evidence'])
    async def no_agent(*a):
        pytest.fail('refused knowledge reached Agent')
    monkeypatch.setattr(workflow,'resolve_node',understand)
    monkeypatch.setattr(workflow,'classify_node',classify)
    monkeypatch.setattr(workflow,'retrieve_knowledge',retrieve)
    monkeypatch.setattr(workflow,'decide_node',no_agent)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'graph.sqlite')) as saver:
        graph = workflow.build_workflow(saver)
        result = await graph.ainvoke(current|{'filters':{},'resumed':False},
                                    {'configurable':{'thread_id':'s'}},context=ctx)
    assert result['refused'] and 'agent_decide' not in result['node_trace']
    assert result['answer_message_id'].isdigit()
    with ctx.session_factory() as db:
        assert db.scalar(select(LowConfidenceQuestion)).retrieved_chunks['chunks'][0]['text']
