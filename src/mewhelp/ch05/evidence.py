"""Reuse the calibrated retriever; reject weak evidence before any streamed answer."""

import asyncio
import math

from pydantic import BaseModel

from mewhelp.knowledge.answering import SourceDTO, source_dtos
from mewhelp.knowledge.filters import SearchFilters
from mewhelp.knowledge.query import QueryUnderstanding
from mewhelp.knowledge.refusals import RefusalInput, record_refusal
from mewhelp.knowledge.retrieval import retrieve_evidence


class EvidenceEnvelope(BaseModel):
    sources: list[SourceDTO]
    scores: list[float | None]
    threshold: float
    context_budget: int
    unsupported_reason: str | None = None


class GateDecision(BaseModel):
    passed: bool
    reason_code: str | None
    reason: str
    top_score: float | None


async def retrieve_knowledge(question: str, *, rag, filters: SearchFilters) -> EvidenceEnvelope:
    query = QueryUnderstanding(question, question, question, "knowledge", ["ch05_passthrough"])
    result = await asyncio.to_thread(retrieve_evidence, rag.retrieval, query, filters)
    sources = source_dtos(result)
    scores = {str(r.chunk.id): r.score for r in result.final}
    return EvidenceEnvelope(
        sources=sources,
        scores=[scores[s.chunk_id] for s in sources],
        threshold=rag.relevance_threshold,
        context_budget=rag.context_budget,
        unsupported_reason=result.unsupported_context_reason,
    )


def evaluate_gate(evidence: EvidenceEnvelope, *, prompt_bytes: int) -> GateDecision:
    if not math.isfinite(evidence.threshold) or evidence.context_budget <= 0:
        raise ValueError("invalid calibrated threshold or context budget")
    if len(evidence.sources) != len(evidence.scores) or any(
        s is None or not math.isfinite(s) for s in evidence.scores
    ):
        raise ValueError("reranker relevance scores missing or invalid")
    top = max(evidence.scores, default=None)
    if evidence.unsupported_reason or prompt_bytes > evidence.context_budget:
        return GateDecision(
            passed=False,
            reason_code="unsupported_context_size",
            reason=evidence.unsupported_reason or "知识证据超出上下文预算",
            top_score=top,
        )
    if not evidence.sources:
        return GateDecision(
            passed=False, reason_code="no_evidence", reason="没有检索到可用证据", top_score=None
        )
    if top < evidence.threshold:
        return GateDecision(
            passed=False, reason_code="low_relevance", reason="检索证据低于校准阈值", top_score=top
        )
    return GateDecision(passed=True, reason_code=None, reason="检索证据达到校准阈值", top_score=top)


async def persist_refusal(context, state: dict, gate: GateDecision) -> str:
    return await asyncio.to_thread(
        record_refusal,
        context.session_factory,
        RefusalInput(
            original_question=state["question"],
            source_conversation_id=state["conversation_id"],
            entry_point=state["entry_point"],
            trigger_stage="retrieval",
            reason_code=gate.reason_code,
            reason=gate.reason,
        ),
    )
