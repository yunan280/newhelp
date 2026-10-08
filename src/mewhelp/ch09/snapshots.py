"""Preserve this round's original text and rank, never re-run historical retrieval."""
from copy import deepcopy

from .contracts import EvidenceSnapshot, RetrievedChunk


def snapshot_result(result, *, query, filters, top_k=5, confidence=None):
    if top_k < 1:
        raise ValueError('top_k must be positive')
    chunks = [RetrievedChunk(rank=rank, chunk_id=str(item.chunk.id), text=item.chunk.text,
                questions=item.chunk.questions, answer=item.chunk.answer,
                section_path=item.chunk.section_path, content_hash=item.chunk.content_hash,
                relevance_score=item.score)
              for rank, item in enumerate(result.final[:top_k], start=1)]
    state = 'empty' if not chunks else ('legacy_partial' if any(
        c.relevance_score is None for c in chunks) else 'captured')
    return EvidenceSnapshot(state=state, chunks=chunks, query=query,
        filters=filters.model_dump(exclude_none=True), top_k=top_k, confidence=confidence,
        reason='原记录未保存精排分数' if state == 'legacy_partial' else None)


def immutable_message_snapshot(snapshot):
    if snapshot is None:
        return None
    value = deepcopy(snapshot)
    value.pop('feedback_lcq_id', None)
    value.pop('trace_id', None)
    return value


def resolve_evidence_snapshot(raw, *, retrieval_performed=None, citations=None):
    if raw is not None:
        return EvidenceSnapshot.model_validate(raw)
    if retrieval_performed is False:
        return None
    if citations:
        chunks = [RetrievedChunk(rank=i, chunk_id=str(c['chunk_id']),
            text=f"分类：{c.get('category', '')}\n问题：{c.get('questions', '')}\n答案：{c.get('answer', '')}",
            questions=c.get('questions', ''), answer=c.get('answer', ''),
            section_path=c.get('section_path'), content_hash=c.get('content_hash'),
            relevance_score=None) for i, c in enumerate(citations, 1)]
        return EvidenceSnapshot(state='legacy_partial', chunks=chunks,
                                reason='仅恢复明确绑定的旧引用，原记录未保存评分')
    return EvidenceSnapshot(state='unavailable', reason='旧记录未保存且无法恢复准确同轮检索片段')
