import json
import time

import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

from mewhelp.ch05.evidence import EvidenceEnvelope
from mewhelp.ch05.limits import TokenUsage
from mewhelp.ch09.snapshots import snapshot_result
from mewhelp.knowledge.answering import REFUSAL_MESSAGE, source_dtos
from mewhelp.knowledge.filters import SearchFilters
from mewhelp.knowledge.refusals import LowConfidenceQuestion
from mewhelp.knowledge.retrieval import RankedChunk, RetrievalResult
from mewhelp.knowledge.store import KnowledgeChunk, snapshot_chunk


def state(cid):
    chunk = snapshot_chunk(KnowledgeChunk(id=1, category='客服', questions='保修多久',
        answer='A1保修两年', section_path='A1保修', content_type='faq', is_key_clause=False))
    raw = RetrievalResult([chunk], [RankedChunk(chunk, .9)])
    snapshot = snapshot_result(raw, query='A1保修多久', filters=SearchFilters()).model_dump(mode='json')
    evidence = EvidenceEnvelope(sources=source_dtos(raw), scores=[.9], threshold=.999,
                               context_budget=32000, retrieved_chunks=snapshot)
    return {'question': '我的A1保修多久', 'resolved_question': 'A1保修多久', 'conversation_id': cid,
        'session_id': 's', 'user_id': 'u', 'turn_id': 'turn', 'route': 'knowledge', 'intent': '商品咨询',
        'entry_point': 'agent', 'evidence': evidence.model_dump(), 'gate': {'passed': True},
        'usage': TokenUsage().model_dump(), 'calls': {'answer': 0}, 'started_at': time.time()}


class AssessmentModel:
    def __init__(self, parsed):
        self.parsed, self.invocations, self.inputs = parsed, 0, None

    def with_structured_output(self, schema, **kwargs):
        assert kwargs['include_raw'] is True
        self.schema = schema
        return self

    async def ainvoke(self, messages):
        self.invocations += 1; self.inputs = messages
        return {'parsed': self.schema.model_validate(self.parsed), 'parsing_error': None,
                'raw': AIMessage(content=json.dumps(self.parsed, ensure_ascii=False),
                    usage_metadata={'input_tokens': 101, 'output_tokens': 31, 'total_tokens': 132})}


@pytest.mark.asyncio
@pytest.mark.parametrize('parsed,code', [
    ({'answerable': False, 'reason': '缺失该型号规定', 'answer': '未经验证的草稿', 'citation_numbers': []}, 'insufficient_evidence'),
    ({'answerable': True, 'reason': '有依据', 'answer': '未经验证的草稿[2]。', 'citation_numbers': [2]}, 'invalid_citation'),
])
async def test_insufficiency_and_invalid_citation_commit_before_fallback(generation_context, parsed, code):
    from mewhelp.ch09.generation import generate_knowledge_answer
    ctx, cid = generation_context
    model = AssessmentModel(parsed)
    ctx.model_factory = lambda *a, **kw: model
    emissions = []
    def emit(value):
        with ctx.session_factory() as db:
            assert db.scalar(select(LowConfidenceQuestion)) is not None
        emissions.append(value)
    current = state(cid)
    current['evidence']['retrieved_chunks']['confidence'] = {'passed':True,'value':.8}
    result = await generate_knowledge_answer(current, ctx, emit)
    assert result['refused'] and result['answer'] == REFUSAL_MESSAGE
    assert model.invocations == 1
    assert '未经验证的草稿' not in json.dumps(emissions, ensure_ascii=False)
    with ctx.session_factory() as db:
        row = db.scalar(select(LowConfidenceQuestion))
        assert row.reason_code == code and row.trigger_stage == 'generation'
        assert str(row.id) == result['low_confidence_question_id']
        assert row.retrieved_chunks['chunks'][0]['relevance_score'] == .9
        assert row.retrieved_chunks['confidence'] == {'passed':True,'value':.8}


@pytest.mark.asyncio
async def test_formal_pass_skips_old_score_gate_once_and_counts_actual_usage(generation_context):
    from mewhelp.ch09.generation import generate_knowledge_answer
    ctx, cid = generation_context
    model = AssessmentModel({'answerable': True, 'reason': 'A1规定完整',
                             'answer': 'A1保修两年[1]。', 'citation_numbers': [1]})
    ctx.model_factory = lambda *a, **kw: model
    result = await generate_knowledge_answer(state(cid), ctx, lambda x: None)
    assert not result['refused'] and result['answer'] == 'A1保修两年[1]。'
    assert model.invocations == 1 and result['calls']['answer'] == 1
    assert result['usage'] == {'input_tokens': 101, 'output_tokens': 31, 'estimated': False}
    assert result['knowledge_raw_usage']['input_tokens'] == 101


@pytest.mark.asyncio
async def test_evaluation_insufficiency_has_no_online_pool_side_effect(generation_context):
    from mewhelp.ch09.generation import generate_knowledge_answer
    ctx, cid = generation_context
    model = AssessmentModel({'answerable': False, 'reason': '缺失规定', 'answer': '', 'citation_numbers': []})
    ctx.model_factory = lambda *a, **kw: model
    result = await generate_knowledge_answer(state(cid), ctx, lambda x: None, record_pool=False)
    assert result['refused'] and result['low_confidence_question_id'] is None
    with ctx.session_factory() as db:
        assert db.scalar(select(LowConfidenceQuestion)) is None
