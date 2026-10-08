"""One evidence-bound generation and one durable refusal path per turn."""

import asyncio
import json
import math
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session

from .filters import SearchFilters
from .prompts import ANSWER_SYSTEM
from .query import QueryUnderstanding
from .refusals import ReasonCode, RefusalInput, record_refusal
from .reranking import UnsupportedContextError
from .retrieval import RankedChunk, RetrievalResult, RetrievalRuntime, retrieve_evidence
from .store import KnowledgeChunk, snapshot_chunk
from .vectors import Strategy
from mewhelp.ch09.observability import observed_sync_call, trace_knowledge

REFUSAL_MESSAGE = (
    "目前知识库没有足够可靠的依据回答这个问题，我无法确认。请补充具体信息或联系人工客服核实。"
)


class SourceDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    number: int
    chunk_id: str
    questions: str
    answer: str
    section_path: str
    category: str
    product_category: str | None
    content_hash: str
    source_url: str


class AnswerAssessment(BaseModel):
    """First assess complete evidence sufficiency, then produce a cited answer."""

    model_config = ConfigDict(extra="forbid", strict=True)
    answerable: bool = Field(description="证据是否足以完整回答当前问题")
    reason: str = Field(min_length=1, max_length=4096, description="充分/不充分的具体理由")
    answer: str = Field(description="可答时的中文回答；不可答时为空字符串")
    citation_numbers: list[int] = Field(description="回答实际使用的来源编号，去重")


@dataclass(frozen=True)
class QuestionContext:
    original_question: str
    conversation_id: int | None
    entry_point: Literal["chat_stream", "agent", "cli"]


@dataclass(frozen=True)
class AnswerResult:
    answer: str
    sources: list[SourceDTO]
    refused: bool
    low_confidence_question_id: str | None
    retrieval: RetrievalResult


@dataclass(frozen=True)
class RagRuntime:
    retrieval: RetrievalRuntime
    generate: Callable[[list[BaseMessage]], Awaitable[AnswerAssessment | None]]
    session_factory: Callable[[], Session]
    relevance_threshold: float
    context_budget: int


async def generate_assessment(
    messages: list[BaseMessage], *, model: Any | None = None
) -> AnswerAssessment | None:
    if model is None:
        from mewhelp.llm import get_structured_model

        model = get_structured_model(AnswerAssessment, include_raw=True)
    envelope = await model.ainvoke(messages)
    if not isinstance(envelope, dict) or envelope.get("parsing_error"):
        return None
    parsed = envelope.get("parsed")
    if isinstance(parsed, BaseModel):
        parsed = parsed.model_dump()
    try:
        result = AnswerAssessment.model_validate(parsed)
        return result if result.reason.strip() else None
    except ValidationError:
        return None


def get_rag_runtime(
    session_factory: Callable[[], Session],
    *,
    calibration_path: Path,
    collection: str | None = None,
) -> RagRuntime:
    from mewhelp.config import get_settings

    from .embedding import embed_texts
    from .reranking import rerank_chunks, reranker_metadata
    from .vectors import MilvusSettings

    settings = get_settings()
    if settings.rag_context_budget is None:
        raise RuntimeError("RAG_CONTEXT_BUDGET must be explicitly configured")
    if not calibration_path.is_file():
        raise RuntimeError("RAG calibration artifact is unavailable")
    threshold = load_relevance_threshold(calibration_path, reranker_metadata())
    index = MilvusSettings().connect_hybrid(collection=collection)
    index.ensure_collection()
    retrieval = RetrievalRuntime(session_factory, embed_texts, index, rerank_chunks)
    return RagRuntime(
        retrieval, generate_assessment, session_factory, threshold, settings.rag_context_budget
    )


def load_relevance_threshold(path: Path, expected_model_metadata: dict) -> float:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("relevance calibration file missing or invalid") from exc
    if not isinstance(record, dict):
        raise TypeError("invalid calibration object")
    threshold = record.get("threshold")
    if (
        type(threshold) not in (int, float)
        or not math.isfinite(threshold)
        or record.get("model_metadata") != expected_model_metadata
        or any(
            not isinstance(record.get(key), str) or not re.fullmatch(r"[0-9a-f]{64}", record[key])
            for key in ("corpus_hash", "query_hash")
        )
    ):
        raise ValueError("calibration threshold/hash/model metadata invalid or mismatched")
    return float(threshold)


def layout_indices(count: int) -> list[int]:
    if count < 0:
        raise ValueError("evidence count cannot be negative")
    return [*range(1, count + 1, 2), *reversed(range(2, count + 1, 2))]


def source_dtos(evidence: RetrievalResult) -> list[SourceDTO]:
    sources, seen = [], set()
    for ranked in evidence.final:
        chunk = ranked.chunk
        if chunk.id in seen:
            continue
        seen.add(chunk.id)
        path = (chunk.section_path or "").strip() or " / ".join(
            part for part in (chunk.content_type, chunk.category, chunk.questions) if part
        )
        sources.append(
            SourceDTO(
                number=len(sources) + 1,
                chunk_id=str(chunk.id),
                questions=chunk.questions,
                answer=chunk.answer,
                section_path=path,
                category=chunk.category,
                product_category=chunk.product_category,
                content_hash=chunk.content_hash,
                source_url=f"/kb/source/{chunk.id}",
            )
        )
    return sources


def answering_messages(query: QueryUnderstanding, sources: list[SourceDTO]) -> list[BaseMessage]:
    by_number = {source.number: source for source in sources}
    payload = {
        "original_question": query.original,
        "canonical_question": query.canonical,
        "evidence": [by_number[number].model_dump() for number in layout_indices(len(sources))],
    }
    return [
        SystemMessage(content=ANSWER_SYSTEM),
        HumanMessage(content=json.dumps(payload, ensure_ascii=False)),
    ]


def _budget_upper_bound(messages: list[BaseMessage]) -> int:
    # A conservative byte-token bound for the provider's BPE, including the tool schema/roles.
    return (
        sum(len(str(message.content).encode("utf-8")) + 64 for message in messages)
        + len(json.dumps(AnswerAssessment.model_json_schema(), ensure_ascii=False).encode("utf-8"))
        + 256
    )


def _citations_valid(assessment: AnswerAssessment, sources: list[SourceDTO]) -> bool:
    inline = [int(item) for item in re.findall(r"\[(\d+)\]", assessment.answer)]
    claimed = assessment.citation_numbers
    allowed = {item.number for item in sources}
    if (
        not inline
        or len(claimed) != len(set(claimed))
        or set(inline) != set(claimed)
        or not set(inline) <= allowed
    ):
        return False
    # Attach postfix citations to their sentence before checking uncited factual text.
    normalized = re.sub(r"([。！？])\s*((?:\[\d+\])+)", r"\2\1", assessment.answer)
    sentences = [item.strip() for item in re.split(r"[。！？\n；]", normalized) if item.strip()]
    return all(re.search(r"\[\d+\]", item) for item in sentences)


@trace_knowledge
async def answer_question(
    runtime: RagRuntime,
    query: QueryUnderstanding,
    *,
    filters: SearchFilters,
    context: QuestionContext,
    strategy: Strategy = "hybrid_rerank",
    apply_relevance_gate: bool = True,
    record_pool: bool = True,
    evidence: RetrievalResult | None = None,
    generation_messages: list[BaseMessage] | None = None,
    refusal_snapshot: dict | None = None,
) -> AnswerResult:
    if context.original_question != query.original:
        raise ValueError("question context does not match this turn")
    if runtime.context_budget < 1:
        raise ValueError("context budget must be positive")
    result = evidence if evidence is not None else RetrievalResult([], [])

    async def refuse(
        stage: Literal["retrieval", "generation"], code: ReasonCode, reason: str
    ) -> AnswerResult:
        pool_id = None
        if record_pool:
            pool_id = await asyncio.to_thread(
                record_refusal,
                runtime.session_factory,
                RefusalInput(
                    context.original_question,
                    context.conversation_id,
                    context.entry_point,
                    stage,
                    code,
                    reason,
                    retrieved_chunks=refusal_snapshot if refusal_snapshot is not None else _refusal_snapshot(result, query, filters),
                ),
            )
        return AnswerResult(REFUSAL_MESSAGE, [], True, pool_id, result)

    if evidence is None:
        try:
            result = await asyncio.to_thread(
                observed_sync_call, 'retrieval.legacy_hybrid_rerank',
                {'question': query.original, 'filters': filters.model_dump()},
                retrieve_evidence, runtime.retrieval, query, filters, strategy=strategy
            )
        except UnsupportedContextError as exc:
            return await refuse("retrieval", "unsupported_context_size", str(exc))
    if result.unsupported_context_reason is not None:
        return await refuse(
            "retrieval", "unsupported_context_size", result.unsupported_context_reason
        )
    if not result.final:
        return await refuse("retrieval", "no_evidence", "没有有效且已发布的来源证据")
    if apply_relevance_gate:
        scores = [item.score for item in result.final]
        if any(score is None or not math.isfinite(score) for score in scores) or not math.isfinite(
            runtime.relevance_threshold
        ):
            raise RuntimeError(
                "production relevance gate requires verified reranker scores and calibration"
            )
        if max(scores) < runtime.relevance_threshold:
            return await refuse("retrieval", "low_relevance", "最高重排分数低于校准阈值")
    sources = source_dtos(result)
    messages = generation_messages if generation_messages is not None else answering_messages(query, sources)
    if _budget_upper_bound(messages) > runtime.context_budget:
        return await refuse(
            "generation",
            "unsupported_context_size",
            "完整 Prompt/问题/证据超过配置预算，未截断证据",
        )
    assessment = await runtime.generate(messages)
    if assessment is None or not isinstance(assessment, AnswerAssessment):
        return await refuse("generation", "invalid_generation", "生成结果不能解析为有效结构")
    if not assessment.answerable:
        return await refuse("generation", "insufficient_evidence", assessment.reason)
    if not assessment.answer.strip():
        return await refuse("generation", "invalid_generation", "可答判断对应的答案为空")
    if not _citations_valid(assessment, sources):
        return await refuse(
            "generation", "invalid_citation", "引用缺失、越界、声明不一致或存在未引用语句"
        )
    return AnswerResult(assessment.answer.strip(), sources, False, None, result)


def _refusal_snapshot(result, query, filters):
    from mewhelp.ch09.snapshots import snapshot_result
    return snapshot_result(result, query=query.canonical, filters=filters).model_dump(mode='json')


async def check_answer_samples(path: Path) -> int:
    """Run labeled prompt samples on the configured real model, with full audit output."""
    from mewhelp.llm import get_structured_model

    upstream = get_structured_model(AnswerAssessment, include_raw=True)
    output = path.with_name("answering-results.jsonl")
    failures = 0
    with output.open("w", encoding="utf-8") as target:
        for line in path.read_text(encoding="utf-8").splitlines():
            sample = json.loads(line)
            captured = {}

            class Capture:
                def __init__(self, record):
                    self.record = record

                async def ainvoke(self, messages):
                    envelope = await upstream.ainvoke(messages)
                    self.record.update(envelope)
                    return envelope

            sample_model = Capture(captured)

            async def generate(messages, model=sample_model):
                return await generate_assessment(messages, model=model)

            chunks = [
                snapshot_chunk(
                    KnowledgeChunk(
                        id=i + 1,
                        category="标注样例",
                        questions=item["question"],
                        answer=item["answer"],
                        section_path=item.get("path", f"样例 / {sample['id']} / {i + 1}"),
                        content_type="manual",
                        product_category=None,
                        is_key_clause=False,
                    )
                )
                for i, item in enumerate(sample["evidence"])
            ]
            evidence = RetrievalResult(chunks, [RankedChunk(chunk, None) for chunk in chunks])
            question = sample["question"]
            query = QueryUnderstanding(question, question, question, "knowledge", [])
            runtime = RagRuntime(None, generate, None, 0.0, 32000)
            result = await answer_question(
                runtime,
                query,
                filters=SearchFilters(),
                context=QuestionContext(question, None, "cli"),
                apply_relevance_gate=False,
                record_pool=False,
                evidence=evidence,
            )
            parsed = captured.get("parsed")
            raw = parsed.model_dump() if isinstance(parsed, BaseModel) else parsed
            sufficient = bool(raw and raw.get("answerable"))
            passed = (
                sufficient == sample["answerable"]
                and result.refused != sample["answerable"]
                and all(text in result.answer for text in sample.get("must_include", []))
                and all(text not in result.answer for text in sample.get("must_not_include", []))
            )
            failures += not passed
            row = {
                "id": sample["id"],
                "question": question,
                "raw": raw,
                "raw_message": captured["raw"].model_dump(mode="json")
                if captured.get("raw")
                else None,
                "parsing_error": str(captured.get("parsing_error") or ""),
                "answer": result.answer,
                "sources": [item.model_dump() for item in result.sources],
                "refused": result.refused,
                "passed": passed,
            }
            target.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(
                json.dumps(
                    {key: row[key] for key in ("id", "passed", "answer", "refused")},
                    ensure_ascii=False,
                ),
                flush=True,
            )
    print(f"failed={failures}; results={output}")
    return failures
