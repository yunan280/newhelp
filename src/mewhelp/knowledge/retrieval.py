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


def _authoritative_chunks(session, hits, filters):
    """One authority check shared by single-query and multi-query retrieval."""
    seen = set()
    for hit in hits:
        row = session.get(KnowledgeChunk, hit.id)
        if (row is None or row.id in seen or row.vectorize_status != "done"
            or row.vector_id != str(row.id)
            or (row.section_path or "").startswith("__deleting__::")):
            continue
        snapshot = snapshot_chunk(row)
        if snapshot.content_hash != hit.content_hash or not filters.matches(snapshot):
            continue
        seen.add(row.id)
        yield snapshot


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
    with runtime.session_factory() as session:
        candidates = list(_authoritative_chunks(session, hits[:50], filters))
    if strategy == "hybrid_rerank" and candidates:
        final = runtime.rerank(query.canonical, candidates)[:10]
    else:
        final = [RankedChunk(item, None) for item in candidates[:10]]
    return RetrievalResult(candidates, final)


def retrieve_multi_evidence(runtime, queries, filters, *, rerank_question, candidate_limit=50):
    if not 1 <= candidate_limit <= 50 or not queries or len(queries) > 5:
        raise ValueError("multi-query retrieval requires 1..5 queries and 1..50 candidates")
    vectors = runtime.embed([q.canonical for q in queries])
    if len(vectors) != len(queries) or any(len(v) != 1024 for v in vectors):
        raise ValueError("BGE-M3 query vectors must have 1024 dimensions")
    scores, snapshots = {}, {}
    with runtime.session_factory() as session:
        for query, vector in zip(queries, vectors, strict=True):
            hits = runtime.index.search("hybrid", vector=vector,
                                        bm25_query=query.bm25_query, filters=filters)
            ranks = {}
            for rank, hit in enumerate(hits[:50], 1):
                ranks.setdefault(hit.id, rank)
            for chunk in _authoritative_chunks(session, hits[:50], filters):
                snapshots[chunk.id] = chunk
                scores[chunk.id] = scores.get(chunk.id, 0.) + 1. / (60 + ranks[chunk.id])
    ids = sorted(snapshots, key=lambda i: (-scores[i], not snapshots[i].is_key_clause, i))
    candidates = [snapshots[i] for i in ids[:candidate_limit]]
    ranked = runtime.rerank(rerank_question, candidates) if candidates else []
    final, seen = [], set()
    authoritative = {c.id: c for c in candidates}
    for item in ranked:
        if item.chunk.id in authoritative and item.chunk.id not in seen:
            seen.add(item.chunk.id)
            final.append(RankedChunk(authoritative[item.chunk.id], item.score))
    return RetrievalResult(candidates, final[:10])


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
