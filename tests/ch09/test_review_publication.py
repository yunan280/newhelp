from sqlalchemy import select

from mewhelp.db.models import ReviewQueue
from mewhelp.knowledge.store import KnowledgeChunk


def test_vector_failure_preserves_approval_and_pending_then_retry(ch09_db):
    from mewhelp.ch09.reviews import ApproveReviewRequest, approve_review, retry_publication

    with ch09_db.begin() as db:
        row = ReviewQueue(normalized_question="问题？")
        db.add(row)
        db.flush()
        rid = row.id

    def fail(ids):
        raise ConnectionError("Milvus unavailable")

    failed = approve_review(
        ch09_db,
        rid,
        ApproveReviewRequest(approved_answer="核准答案"),
        publish=fail,
        verify_published=lambda i: False,
    )
    assert (
        failed.review_status == "通过"
        and failed.publication_status == "pending"
        and failed.sync_error
    )
    with ch09_db() as db:
        assert db.get(ReviewQueue, rid).approved_answer == "核准答案"
        assert db.get(KnowledgeChunk, int(failed.knowledge_id)).vectorize_status == "pending"

    def publish(ids):
        assert ids == [int(failed.knowledge_id)]
        with ch09_db.begin() as db:
            row = db.get(KnowledgeChunk, ids[0])
            row.vectorize_status = "done"
            row.vector_id = str(row.id)

    retried = retry_publication(ch09_db, rid, publish=publish, verify_published=lambda i: True)
    assert retried.knowledge_id == failed.knowledge_id and retried.publication_status == "published"
    with ch09_db() as db:
        assert len(db.scalars(select(KnowledgeChunk)).all()) == 1


def test_mysql_done_but_vector_invisible_requires_compensation(ch09_db):
    from mewhelp.ch09.reviews import ApproveReviewRequest, approve_review, retry_publication

    with ch09_db.begin() as db:
        row = ReviewQueue(normalized_question="问题？")
        db.add(row)
        db.flush()
        rid = row.id

    def mark(ids):
        with ch09_db.begin() as db:
            row = db.get(KnowledgeChunk, ids[0])
            row.vectorize_status = "done"
            row.vector_id = str(row.id)

    result = approve_review(
        ch09_db,
        rid,
        ApproveReviewRequest(approved_answer="答案"),
        publish=mark,
        verify_published=lambda i: False,
    )
    assert result.publication_status == "pending" and result.sync_error
    with ch09_db() as db:
        assert db.get(KnowledgeChunk, int(result.knowledge_id)).vectorize_status == "pending"
    assert (
        retry_publication(
            ch09_db, rid, publish=mark, verify_published=lambda i: True
        ).publication_status
        == "published"
    )
