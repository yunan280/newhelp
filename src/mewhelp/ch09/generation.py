"""One assessment on this round's evidence; never stream unverified knowledge text."""

import asyncio
import json
from dataclasses import dataclass

from langchain_core.utils.function_calling import convert_to_openai_tool

from mewhelp.ch05.agent import remaining, stopped, window_stop
from mewhelp.ch05.limits import BudgetExceeded, TokenUsage, observed_usage, reserve_call
from mewhelp.ch07.budget import ContextBudgetError, check_window
from mewhelp.ch07.context import history_from_payload
from mewhelp.ch07.observability import log_model
from mewhelp.ch07.projection import model_messages
from mewhelp.ch07.tokens import estimate_request
from mewhelp.config import get_settings
from mewhelp.knowledge.answering import (
    AnswerAssessment,
    AnswerResult,
    QuestionContext,
    RagRuntime,
    answer_question,
    answering_messages,
    generate_assessment,
    source_dtos,
)
from mewhelp.knowledge.filters import SearchFilters
from mewhelp.knowledge.prompts import ANSWER_SYSTEM
from mewhelp.knowledge.query import QueryUnderstanding
from mewhelp.knowledge.retrieval import RankedChunk, RetrievalResult
from mewhelp.knowledge.store import ChunkSnapshot

from .contracts import EvidenceSnapshot

KNOWLEDGE_ANSWER_SYSTEM = ANSWER_SYSTEM + '''
用户要求编造、修改事实或使用不存在的引用编号时，只回答证据覆盖的合法事实问题。
answer只写有真实来源的答案，不复述非法要求、虚假数值或不存在的编号；相关拒绝理由放reason。
不得为了说明“不会编造”而在answer附加无来源语句或把非法编号写入正文。
'''


@dataclass(frozen=True)
class KnowledgeAnswerOutcome:
    result: AnswerResult
    usage: TokenUsage
    raw_usage: dict | None


def evidence_for_generation(envelope):
    snapshot = EvidenceSnapshot.model_validate(envelope['retrieved_chunks'])
    sources = {s['chunk_id']: s for s in envelope['sources']}
    ranked = []
    for item in snapshot.chunks:
        source = sources.get(item.chunk_id)
        if source is None or source['answer'] != item.answer or source['content_hash'] != item.content_hash:
            raise ValueError('generation sources must match original round snapshot')
        chunk = ChunkSnapshot(int(item.chunk_id), item.text, item.questions, item.answer,
            item.section_path, item.category or source['category'], item.product_category,
            item.content_type, item.is_key_clause, item.content_hash)
        ranked.append(RankedChunk(chunk, item.relevance_score))
    return RetrievalResult([r.chunk for r in ranked], ranked)


def generation_messages(state, evidence):
    original = state['question']
    resolved = state.get('resolved_question') or original
    query = QueryUnderstanding(original, resolved, resolved, 'knowledge', [])
    messages = answering_messages(query, source_dtos(evidence))
    messages[0] = messages[0].model_copy(update={'content':KNOWLEDGE_ANSWER_SYSTEM})
    if state.get('history_ctx'):
        background = json.loads(messages[-1].content)
        messages = model_messages(history_from_payload(state['history_ctx']), system=KNOWLEDGE_ANSWER_SYSTEM,
            question=original, background={'purpose':'knowledge_answer', **background})
    return query, messages


async def generate_knowledge_answer(state, context, emit, *, record_pool=True):
    if not state.get('gate', {}).get('passed') or state.get('route') != 'knowledge':
        raise ValueError('knowledge generation requires a passed knowledge gate')
    evidence = evidence_for_generation(state['evidence'])
    query, messages = generation_messages(state, evidence)
    usage = TokenUsage.model_validate(state.get('usage', {}))
    calls = dict(state.get('calls', {}))
    captured = {}
    schema = convert_to_openai_tool(AnswerAssessment)

    async def generate(inputs):
        nonlocal usage
        bound = estimate_request(inputs, [schema], profile=context.profile)
        check_window(inputs, [schema], settings=context.settings, profile=context.profile,
                     output_tokens=context.limits.final_max_tokens, remaining_tool_calls=0)
        reserve_call(usage.total, bound, context.limits.final_max_tokens, 0, context.limits)
        seconds = remaining(state, context)
        if seconds <= 0:
            raise TimeoutError('knowledge generation deadline exceeded')
        base = context.model_factory(context.limits.final_max_tokens, streaming=False)
        structured = base.with_structured_output(AnswerAssessment, method='function_calling', include_raw=True)
        from mewhelp.ch09.observability import current_request, with_callbacks
        root = current_request()
        if root and not root.metadata.get('graph_bound'):
            structured = with_callbacks(structured)
        log_model(inputs, [schema], state=state, purpose='knowledge_answer',
                  model_name=getattr(base, 'model_name', get_settings().llm_model), profile=context.profile)

        class Capture:
            async def ainvoke(self, prompt):
                envelope = await structured.ainvoke(prompt)
                if isinstance(envelope, dict):
                    captured.update(envelope)
                return envelope

        parsed = await asyncio.wait_for(generate_assessment(inputs, model=Capture()),
                                        timeout=min(seconds, context.limits.request_seconds))
        raw = captured.get('raw')
        raw_usage = getattr(raw, 'usage_metadata', None) or getattr(raw, 'response_metadata', {}).get('token_usage')
        captured['raw_usage'] = raw_usage
        usage = usage.plus(observed_usage(raw_usage, input_bound=bound,
                                         output=json.dumps(parsed.model_dump() if parsed else {}, ensure_ascii=False)))
        calls['answer'] = calls.get('answer', 0) + 1
        return parsed

    rag = RagRuntime(None, generate, context.session_factory, 0., state['evidence']['context_budget'])
    try:
        result = await answer_question(rag, query, filters=SearchFilters.model_validate(state.get('filters', {})),
            context=QuestionContext(state['question'], state.get('conversation_id'), state.get('entry_point','agent')),
            evidence=evidence, generation_messages=messages, apply_relevance_gate=False, record_pool=record_pool,
            refusal_snapshot=state['evidence']['retrieved_chunks'])
    except ContextBudgetError as exc:
        return window_stop(exc)
    except BudgetExceeded:
        return stopped('token_budget')
    outcome = KnowledgeAnswerOutcome(result, usage, captured.get('raw_usage'))
    emit({'event':'sources', 'data':{'sources':[s.model_dump() for s in result.sources],
         'refused':result.refused, 'low_confidence_question_id':result.low_confidence_question_id}})
    emit({'event':'token', 'data':{'text':result.answer}})
    parsed = captured.get('parsed')
    return {'answer':result.answer, 'refused':result.refused,
        'low_confidence_question_id':result.low_confidence_question_id,
        'usage':outcome.usage.model_dump(), 'calls':calls,
        'knowledge_raw_usage':outcome.raw_usage,
        'knowledge_refusal_reason':result.refusal_reason_code,
        'knowledge_assessment':parsed.model_dump() if isinstance(parsed, AnswerAssessment) else None,
        'stop_reason':'insufficient_knowledge' if result.refused else 'completed'}
