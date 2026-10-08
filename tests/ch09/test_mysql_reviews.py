import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from mewhelp.ch09.reviews import ApproveReviewRequest, approve_review, verify_chunk_visible
from mewhelp.db.models import ReviewQueue
from mewhelp.knowledge.store import KnowledgeChunk, snapshot_chunk

pytestmark = pytest.mark.mysql


async def test_mysql_concurrent_approval_same_knowledge(ch09_mysql):
    factory = sessionmaker(ch09_mysql, expire_on_commit=False)
    with factory.begin() as db:
        row = ReviewQueue(normalized_question="问题？")
        db.add(row)
        db.flush()
        rid = row.id
    request = ApproveReviewRequest(approved_answer="核准答案")

    def publish(ids):
        with factory.begin() as db:
            row = db.get(KnowledgeChunk, ids[0])
            row.vectorize_status = "done"
            row.vector_id = str(row.id)

    def approve():
        return approve_review(
            factory, rid, request, publish=publish, verify_published=lambda i: True
        )

    results = await asyncio.gather(*(asyncio.to_thread(approve) for _ in range(2)))
    assert results[0].knowledge_id == results[1].knowledge_id
    with factory() as db:
        assert len(db.scalars(select(KnowledgeChunk)).all()) == 1
        assert db.get(ReviewQueue, rid).review_status == "通过"


def test_real_bge_publication_milvus_retrieves_approved_same_question(ch09_mysql):
    from mewhelp.knowledge.embedding import embed_texts
    from mewhelp.knowledge.filters import SearchFilters
    from mewhelp.knowledge.retrieval import RetrievalRuntime
    from mewhelp.knowledge.sync import sync_pending
    from mewhelp.knowledge.vectors import MilvusSettings

    factory = sessionmaker(ch09_mysql, expire_on_commit=False)
    collection = "ch09_review_test_" + uuid4().hex[:16]
    index = MilvusSettings().connect_hybrid(collection=collection)
    question = "X9星尘传感器首次标定费用是多少？"
    try:
        index.ensure_collection()
        with factory.begin() as db:
            row = ReviewQueue(normalized_question=question)
            db.add(row)
            db.flush()
            rid = row.id

        def publish(ids):
            sync_pending(factory, embed_texts, index, row_ids=ids)

        result = approve_review(
            factory,
            rid,
            ApproveReviewRequest(approved_answer="X9首次标定免费。"),
            publish=publish,
            verify_published=lambda i: verify_chunk_visible(factory, index, i),
        )
        assert result.publication_status == "published", result.sync_error
        runtime = RetrievalRuntime(factory, embed_texts, index, None)
        # BM25 checks the real approved FAQ and current MySQL hash without a synthetic relevance score.
        from mewhelp.knowledge.query import QueryUnderstanding
        from mewhelp.knowledge.retrieval import retrieve_evidence

        evidence = retrieve_evidence(
            runtime,
            QueryUnderstanding(question, question, question, "knowledge", []),
            SearchFilters(),
            strategy="bm25",
        )
        kid = int(result.knowledge_id)
        assert evidence.final and evidence.final[0].chunk.id == kid
        assert evidence.final[0].chunk.answer == "X9首次标定免费。"
        with factory() as db:
            snapshot = snapshot_chunk(db.get(KnowledgeChunk, kid))
        index.upsert(replace(snapshot, content_hash="0" * 64), embed_texts([snapshot.text])[0])
        assert not verify_chunk_visible(factory, index, kid)
    finally:
        assert collection.startswith("ch09_review_test_") and collection.isidentifier()
        if index.client.has_collection(collection):
            index.client.drop_collection(collection)
