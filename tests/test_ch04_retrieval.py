import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from mewhelp.db.base import Base
from mewhelp.knowledge.filters import SearchFilters
from mewhelp.knowledge.query import QueryUnderstanding
from mewhelp.knowledge.store import KnowledgeChunk, KnowledgeDraft, put_chunk, snapshot_chunk
from mewhelp.knowledge.vectors import SearchHit


def environment():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = lambda: Session(engine)
    snapshots = []
    with factory() as session:
        for i in range(25):
            row = put_chunk(
                session,
                KnowledgeDraft(
                    f"retrieval:{i}",
                    "参数",
                    f"HX-{i}",
                    f"规格{i}",
                    "手册",
                    "manual",
                    product_category="耳机",
                ),
            )
            row.vectorize_status = "done"
            row.vector_id = str(row.id)
            snapshots.append(snapshot_chunk(row))
        session.commit()
    return factory, snapshots


def query():
    return QueryUnderstanding("蓝牙啥版本", "蓝牙版本是什么", "蓝牙版本 无线协议", "knowledge", [])


def test_mysql_rejects_missing_pending_tombstone_hash_and_category_changes():
    from mewhelp.knowledge.retrieval import RetrievalRuntime, retrieve_evidence

    factory, chunks = environment()
    with factory() as session:
        row = session.get(KnowledgeChunk, chunks[1].id)
        row.vectorize_status = "pending"
        row = session.get(type(row), chunks[2].id)
        row.section_path = "__deleting__::手册"
        row = session.get(type(row), chunks[3].id)
        row.product_category = "手机"
        row = session.get(type(row), chunks[4].id)
        row.answer = "改过的内容"
        session.commit()

    class Index:
        def search(self, *args, **kwargs):
            return [
                SearchHit(999, "z" * 64),
                *[SearchHit(c.id, c.content_hash) for c in chunks[:5]],
            ]

    runtime = RetrievalRuntime(factory, lambda texts: [[0.1] * 1024], Index(), lambda q, c: [])
    result = retrieve_evidence(
        runtime, query(), SearchFilters(product_category="耳机"), strategy="hybrid"
    )
    assert [c.id for c in result.candidates] == [chunks[0].id]
    assert result.final[0].chunk.answer == "规格0"


@pytest.mark.parametrize("strategy", ["dense", "bm25", "hybrid", "hybrid_rerank"])
def test_strategy_preserves_candidates_and_reranks_only_fourth(strategy):
    from mewhelp.knowledge.retrieval import RankedChunk, RetrievalRuntime, retrieve_evidence

    factory, chunks = environment()
    seen = []
    embeds = []

    class Index:
        def search(self, chosen, **kwargs):
            assert chosen == strategy
            assert kwargs["bm25_query"] == query().bm25_query
            return [SearchHit(c.id, c.content_hash) for c in chunks]

    def embed(texts):
        embeds.extend(texts)
        return [[0.1] * 1024]

    def rerank(q, items):
        seen.append(q)
        return [RankedChunk(c, 0.8) for c in [items[19], *items[:19], *items[20:]]]

    result = retrieve_evidence(
        RetrievalRuntime(factory, embed, Index(), rerank),
        query(),
        SearchFilters(),
        strategy=strategy,
    )
    assert len(result.candidates) == 25 and len(result.final) == 10
    assert result.final[0].chunk.id == chunks[19 if strategy == "hybrid_rerank" else 0].id
    assert seen == (["蓝牙版本是什么"] if strategy == "hybrid_rerank" else [])
    assert embeds == ([] if strategy == "bm25" else ["蓝牙版本是什么"])


def test_empty_result_does_not_invoke_reranker():
    from mewhelp.knowledge.retrieval import RetrievalRuntime, retrieve_evidence

    factory, _ = environment()

    class Index:
        def search(self, *args, **kwargs):
            return []

    def unexpected(*args):
        raise AssertionError("no rerank on empty")

    result = retrieve_evidence(
        RetrievalRuntime(factory, lambda t: [[0.1] * 1024], Index(), unexpected),
        query(),
        SearchFilters(),
    )
    assert result.candidates == result.final == []


def test_wrong_embedding_dimension_is_rejected_before_search():
    from mewhelp.knowledge.retrieval import RetrievalRuntime, retrieve_evidence

    factory, _ = environment()
    with pytest.raises(ValueError, match="1024"):
        retrieve_evidence(
            RetrievalRuntime(factory, lambda t: [[1.0]], None, None), query(), SearchFilters()
        )
