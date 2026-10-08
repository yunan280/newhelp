"""Reuse the calibrated retriever; reject weak evidence before any streamed answer."""

import asyncio
import hashlib
import json
import math
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy import select

from mewhelp.knowledge.answering import SourceDTO, source_dtos
from mewhelp.knowledge.filters import SearchFilters
from mewhelp.knowledge.query import QueryUnderstanding
from mewhelp.knowledge.refusals import RefusalInput, record_refusal
from mewhelp.knowledge.reranking import UnsupportedContextError, reranker_metadata
from mewhelp.knowledge.retrieval import retrieve_evidence, retrieve_multi_evidence
from mewhelp.knowledge.store import KnowledgeChunk, snapshot_chunk
from mewhelp.ch09.observability import observed_sync_call


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


def limit_evidence(evidence: EvidenceEnvelope, *, top_k: int) -> EvidenceEnvelope:
    if top_k < 1:
        raise ValueError('evidence top_k must be positive')
    return evidence.model_copy(update={'sources': evidence.sources[:top_k],
                                       'scores': evidence.scores[:top_k]})


def policy_corpus_hash(session_factory) -> str:
    with session_factory() as session:
        chunks = [snapshot_chunk(row) for row in session.scalars(select(KnowledgeChunk)).all()
                  if row.vectorize_status == "done" and row.vector_id == str(row.id)
                  and not (row.section_path or "").startswith("__deleting__::")
                  and row.category == "退款售后政策" and row.content_type == "policy"]
    payload = [(c.id, c.content_hash) for c in sorted(chunks, key=lambda c: c.id)]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def policy_input_hash() -> str:
    from mewhelp.ch06.evaluation import model_hash
    from mewhelp.ch06.prompts import EXPANSION_SYSTEM
    root = Path(__file__).resolve().parents[1]
    paths = [root / "knowledge" / n for n in ("retrieval.py", "filters.py", "query.py")]
    paths.append(root / "ch06" / "expansion.py")
    payload = b"".join(p.read_text(encoding="utf-8").replace("\r\n", "\n").encode()
                       for p in paths)
    return hashlib.sha256(payload + EXPANSION_SYSTEM.encode() + model_hash().encode()
                          + b"policy:refund-aftersales:original+queries:rrf60:50:10").hexdigest()


def policy_dataset_hash() -> str:
    from mewhelp.ch06.evaluation import verify_dataset
    return verify_dataset(Path(__file__).resolve().parents[3] / "eval" / "ch06")["dataset_hash"]


def validate_policy_calibration(calibration, runtime):
    if (calibration.sample_count != 16
        or calibration.reranker_metadata != reranker_metadata()
        or calibration.corpus_hash != policy_corpus_hash(runtime.session_factory)
        or calibration.retrieval_input_hash != policy_input_hash()
        or calibration.dataset_hash != policy_dataset_hash()):
        raise ValueError("policy calibration does not match current model/corpus/queries/dataset")


async def retrieve_policy(question, order, queries, *, rag, filters, calibration):
    if (filters.content_type not in {None, "policy"}
        or filters.category not in {None, "退款售后政策"}
        or filters.is_key_clause is not None):
        raise ValueError("filters conflict with mandatory complete policy domain")
    await asyncio.to_thread(validate_policy_calibration, calibration, rag.retrieval)
    forced = SearchFilters(category="退款售后政策", content_type="policy",
                           product_category=filters.product_category)
    texts = list(dict.fromkeys([question, *queries]))
    understood = [QueryUnderstanding(t, t, t, "knowledge", ["ch06_policy"]) for t in texts]
    rerank_question = question + "\n订单事实：" + json.dumps(
        order.model_dump(mode="json"), ensure_ascii=False,
    )
    try:
        result = await asyncio.to_thread(observed_sync_call, 'retrieval.policy_hybrid_rerank',
            {'question': question, 'queries': texts, 'filters': forced.model_dump(),
             'rerank_question': rerank_question}, retrieve_multi_evidence,
             rag.retrieval, understood, forced, rerank_question=rerank_question)
    except UnsupportedContextError as exc:
        return EvidenceEnvelope(sources=[], scores=[],
                                threshold=calibration.policy_rerank_threshold,
                                context_budget=rag.context_budget, unsupported_reason=str(exc))
    sources = source_dtos(result)
    scores = {str(r.chunk.id): r.score for r in result.final}
    return EvidenceEnvelope(sources=sources, scores=[scores[s.chunk_id] for s in sources],
                            threshold=calibration.policy_rerank_threshold,
                            context_budget=rag.context_budget,
                            unsupported_reason=result.unsupported_context_reason)


async def retrieve_knowledge(question: str, *, rag, filters: SearchFilters) -> EvidenceEnvelope:
    query = QueryUnderstanding(question, question, question, "knowledge", ["ch05_passthrough"])
    try:
        result = await asyncio.to_thread(observed_sync_call, 'retrieval.hybrid_rerank',
            {'question': question, 'filters': filters.model_dump()},
            retrieve_evidence, rag.retrieval, query, filters)
    except UnsupportedContextError as exc:
        return EvidenceEnvelope(
            sources=[],
            scores=[],
            threshold=rag.relevance_threshold,
            context_budget=rag.context_budget,
            unsupported_reason=str(exc),
        )
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
