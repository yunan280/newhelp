import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from test_generation_boundary import AssessmentModel, state
from test_workflow_confidence import profile

from mewhelp.ch05 import workflow
from mewhelp.ch07.context import tag_message
from mewhelp.ch08.confirmation import prepare_ticket_node
from mewhelp.knowledge.retrieval import RankedChunk, RetrievalResult
from mewhelp.knowledge.store import KnowledgeChunk, snapshot_chunk
from mewhelp.tools.contracts import ToolSpec
from mewhelp.tools.engine import ToolExecutionEngine
from mewhelp.tools.registry import ToolRegistry


def configure(ctx, monkeypatch):
    chunks = [snapshot_chunk(KnowledgeChunk(id=i, category='客服', questions='保修多久',
              answer='A1保修两年'+'原文'*250, section_path='A1保修', content_type='faq',
              is_key_clause=False)) for i in range(1, 11)]
    raw = RetrievalResult(chunks, [RankedChunk(c, .9-i*.01) for i, c in enumerate(chunks)])
    calls = []
    def retrieve(question, *, rag, filters):
        calls.append(question)
        return raw
    monkeypatch.setattr('mewhelp.ch05.evidence.retrieve_current_evidence', retrieve)

    @tool(response_format='content_and_artifact')
    def query_faq(keyword: str) -> tuple[str, object]:
        """查FAQ。"""
        return json.dumps([c.text for c in chunks], ensure_ascii=False), raw

    @tool
    def query_product(product_name: str) -> str:
        """查商品价格。"""
        return '未使用'

    ctx.tool_snapshot = ToolRegistry({t.name: ToolSpec(t, preserve_raw=True)
                                     for t in [query_faq, query_product]}).snapshot()
    ctx.tool_runtime = SimpleNamespace(engine=ToolExecutionEngine())
    ctx.confidence_profile = profile(.4)
    ctx.settings = ctx.settings.model_copy(update={'rerank_top_k':5})
    ctx.rag_factory = lambda: SimpleNamespace(context_budget=32000, relevance_threshold=.99)
    return calls


async def test_faq_artifact_is_not_a_long_tool_payload_or_unguarded_answer(generation_context, monkeypatch):
    ctx, cid = generation_context
    calls = configure(ctx, monkeypatch)
    current = state(cid) | {'route':'business', 'decision_count':1, 'agent_messages':[],
               'tool_trace':[], 'pending_tool_calls':[{'id':'faq','name':'query_faq',
                                                     'args':{'keyword':'模型改写了问题'}}],
               'tool_count':0}
    result = await prepare_ticket_node(current, ctx, lambda event: None)
    assert result.get('stop_reason') != 'tool_result_limit'
    assert result['route'] == 'knowledge' and result['knowledge_tool_gate_pending']
    assert calls == ['A1保修多久']
    assert len(result['retrieved_chunks']['chunks']) == 5
    assert result['retrieved_chunks']['chunks'][0]['relevance_score'] == .9
    assert result['messages'][0].tool_call_id == 'faq'
    assert len(result['messages'][0].content) < 200


async def test_passed_formal_knowledge_gate_has_one_generation_not_second_tool_decision(generation_context, monkeypatch):
    ctx, cid = generation_context
    ctx.confidence_profile = profile(.4)
    async def forbidden(*args):
        pytest.fail('formal evidence was sent back through another tool decision')
    monkeypatch.setattr(workflow, 'decide_agent', forbidden)
    result = await workflow.decide_node(state(cid), ctx, lambda e: None)
    assert result['decision']['reply_mode'] == 'answer' and result['pending_tool_calls'] == []


@pytest.mark.parametrize('threshold, passed',[(.4,True),(.95,False)])
async def test_actual_graph_business_faq_runs_formal_gate_and_same_generation(generation_context, monkeypatch, tmp_path, threshold, passed):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    ctx, cid = generation_context
    calls = configure(ctx, monkeypatch)
    ctx.confidence_profile = profile(threshold)
    model = AssessmentModel({'answerable':True,'reason':'原文明确','answer':'A1保修两年[1]。',
                             'citation_numbers':[1]})
    ctx.model_factory = lambda *a, **kw:model
    async def understand(s,c,e):
        return {'resolved_question':'A1保修多久','scope':'general'}
    async def classify(s,c,e):
        return {'intent':'商品咨询','matched_tool':'query_product'}
    control_calls = []
    async def decide(s,c):
        control_calls.append(True)
        assert len(control_calls) == 1, 'knowledge evidence returned to business loop'
        call={'id':'faq','name':'query_faq','args':{'keyword':'模型的不同问法'}}
        message=AIMessage(content='',tool_calls=[call],id=s['turn_id']+'-request')
        return {'pending_tool_calls':[call],'agent_messages':[message],
                'messages':[tag_message(message,turn_id=s['turn_id'])], 'decision_count':1}
    monkeypatch.setattr(workflow,'resolve_node',understand)
    monkeypatch.setattr(workflow,'classify_node',classify)
    monkeypatch.setattr(workflow,'decide_agent',decide)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'faq.sqlite')) as saver:
        graph=workflow.build_workflow(saver)
        result=await graph.ainvoke(state(cid)|{'filters':{},'resumed':False},
                                  {'configurable':{'thread_id':'s'}},context=ctx)
    from mewhelp.knowledge.answering import REFUSAL_MESSAGE
    assert result['gate']['passed'] is passed, result['gate']
    assert result['answer'] == ('A1保修两年[1]。' if passed else REFUSAL_MESSAGE)
    assert result['node_trace'].index('execute_tools') < result['node_trace'].index('confidence_gate')
    assert result['gate']['evidence_confidence']['passed'] is passed
    assert model.invocations == int(passed) and calls == ['A1保修多久']
    assert result['answer_message_id'].isdigit()


async def test_fallback_after_mixed_ticket_and_faq_retains_actual_receipt(generation_context):
    ctx, cid = generation_context
    current=state(cid)|{'ticket_receipt':{'ticket_no':'DEMO-09'},'low_confidence_question_id':'1'}
    result=await workflow.fallback_node(current,ctx,lambda e:None)
    assert 'DEMO-09' in result['answer'] and '工单已提交' in result['answer']
