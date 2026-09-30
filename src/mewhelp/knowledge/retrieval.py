"""Milvus 只负责候选主键，MySQL 决定可回答的原文。"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from .filters import SearchFilters
from .query import QueryUnderstanding
from .store import ChunkSnapshot, KnowledgeChunk, snapshot_chunk
from .vectors import HybridMilvusIndex, Strategy


@dataclass(frozen=True)
class RankedChunk:
    chunk: ChunkSnapshot
    score: float | None


@dataclass(frozen=True)
class RetrievalResult:
    candidates: list[ChunkSnapshot]
    final: list[RankedChunk]
    unsupported_context_reason: str | None = None


@dataclass(frozen=True)
class RetrievalRuntime:
    session_factory: Callable[[], Session]
    embed: Callable[[list[str]], list[list[float]]]
    index: HybridMilvusIndex
    rerank: Callable[[str, list[ChunkSnapshot]], list[RankedChunk]]


def retrieve_evidence(
    runtime: RetrievalRuntime,
    query: QueryUnderstanding,
    filters: SearchFilters,
    *,
    strategy: Strategy = "hybrid_rerank",
) -> RetrievalResult:
    vector = None
    if strategy != "bm25":
        vectors = runtime.embed([query.canonical])
        if len(vectors) != 1 or len(vectors[0]) != 1024:
            raise ValueError("BGE-M3 query vector must have 1024 dimensions")
        vector = vectors[0]
    hits = runtime.index.search(
        strategy, vector=vector, bm25_query=query.bm25_query, filters=filters
    )
    candidates, seen = [], set()
    with runtime.session_factory() as session:
        for hit in hits[:50]:
            row = session.get(KnowledgeChunk, hit.id)
            if (
                row is None
                or row.id in seen
                or row.vectorize_status != "done"
                or row.vector_id != str(row.id)
                or (row.section_path or "").startswith("__deleting__::")
            ):
                continue
            snapshot = snapshot_chunk(row)
            if snapshot.content_hash != hit.content_hash or not filters.matches(snapshot):
                continue
            seen.add(row.id)
            candidates.append(snapshot)
    if strategy == "hybrid_rerank" and candidates:
        final = runtime.rerank(query.canonical, candidates)[:10]
    else:
        final = [RankedChunk(item, None) for item in candidates[:10]]
    return RetrievalResult(candidates, final)


class VectorSearcher(Protocol):
    def search(self, vector: list[float], limit: int = 3) -> list[int]: ...


# PyMilvus 2.6 要求 limit + offset < 16,384。
_MAX_MILVUS_TOP_K = 16383


def retrieve(
    session_factory: Callable[[], Session],
    embed: Callable[[list[str]], list[list[float]]],
    vectors: VectorSearcher,
    question: str,
    *,
    limit: int = 3,
) -> list[KnowledgeChunk]:
    query_vector = embed([question])[0]
    requested = limit
    while True:
        ids = vectors.search(query_vector, limit=requested)
        if not ids:
            return []
        with session_factory() as session:
            rows = {row_id: session.get(KnowledgeChunk, row_id) for row_id in ids}
            valid = [
                row
                for row_id in ids
                if (row := rows[row_id]) is not None
                and row.vectorize_status == "done"
                and row.vector_id == str(row.id)
                and not (row.section_path or "").startswith("__deleting__::")
            ]
        if len(valid) >= limit or len(ids) < requested or requested >= _MAX_MILVUS_TOP_K:
            return valid[:limit]
        requested = min(requested * 2, _MAX_MILVUS_TOP_K)
