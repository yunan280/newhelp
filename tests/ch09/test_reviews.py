import pytest
from sqlalchemy import event, select

from mewhelp.db.models import ReviewQueue
from mewhelp.knowledge.refusals import LowConfidenceQuestion
from mewhelp.knowledge.store import KnowledgeChunk


def queue(factory):
    with factory.begin() as db:
        row = ReviewQueue(normalized_question="M1保修多久？", ai_suggested_answer="待核准")
        db.add(row)
        db.flush()
        return row.id


def test_approval_same_parameters_one_knowledge_and_changed_content_conflicts(ch09_db):
    from mewhelp.ch09.reviews import ApproveReviewRequest, approve_review

    rid = queue(ch09_db)
    published = []

    def publish(ids):
        published.extend(ids)
        with ch09_db.begin() as db:
            for i in ids:
                row = db.get(KnowledgeChunk, i)
                row.vectorize_status = "done"
                row.vector_id = str(i)

    request = ApproveReviewRequest(approved_answer="保修2年。")
    one = approve_review(ch09_db, rid, request, publish=publish, verify_published=lambda i: True)
    two = approve_review(ch09_db, rid, request, publish=publish, verify_published=lambda i: True)
    assert one.knowledge_id == two.knowledge_id and one.publication_status == "published"
    with ch09_db() as db:
        assert len(db.scalars(select(KnowledgeChunk)).all()) == 1
    for changes in [
        {"approved_answer": "保修3年。"},
        {"category": "其他"},
        {"product_category": "音箱"},
    ]:
        with pytest.raises(ValueError, match="核准"):
            approve_review(
                ch09_db,
                rid,
                request.model_copy(update=changes),
                publish=publish,
                verify_published=lambda i: True,
            )


def test_reject_is_terminal_and_does_not_create_knowledge(ch09_db):
    from mewhelp.ch09.reviews import ApproveReviewRequest, approve_review, reject_review

    rid = queue(ch09_db)
    assert reject_review(ch09_db, rid).review_status == "驳回"
    assert reject_review(ch09_db, rid).review_status == "驳回"
    with pytest.raises(ValueError, match="驳回"):
        approve_review(
            ch09_db,
            rid,
            ApproveReviewRequest(approved_answer="答案"),
            publish=None,
            verify_published=None,
        )
    with ch09_db() as db:
        assert db.scalar(select(KnowledgeChunk)) is None


@pytest.mark.parametrize("answer", ["", "  ", "\n"])
def test_empty_approved_answer_is_rejected(answer):
    from mewhelp.ch09.reviews import ApproveReviewRequest

    with pytest.raises(ValueError):
        ApproveReviewRequest(approved_answer=answer)


def test_detail_paginates_all_originals_with_entry_and_snapshot(ch09_db):
    from mewhelp.ch09.reviews import list_reviews, review_detail

    rid = queue(ch09_db)
    with ch09_db.begin() as db:
        db.add_all(
            LowConfidenceQuestion(
                original_question=f"原话{i}",
                entry_point="cli",
                trigger_stage="retrieval",
                reason_code="low_relevance",
                reason="不足",
                matched_review_id=rid,
                retrieved_chunks={"state": "empty", "chunks": []},
            )
            for i in range(3)
        )
    detail = review_detail(ch09_db, rid, page=2, page_size=2)
    assert detail["originals"]["total"] == 3 and len(detail["originals"]["items"]) == 1
    assert detail["originals"]["items"][0]["retrieved_chunks"]["state"] == "empty"
    listing = list_reviews(ch09_db, status="待审", page=1, page_size=2, sort="occurrence")
    assert listing["total"] == 1 and listing["items"][0]["id"] == str(rid)


def test_approval_transaction_failure_rolls_back_original_and_review(ch09_db):
    from mewhelp.ch09.reviews import ApproveReviewRequest, approve_review

    rid = queue(ch09_db)
    engine = ch09_db.kw["bind"]

    def fail(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("UPDATE REVIEW_QUEUE"):
            raise RuntimeError("injected approval commit failure")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError):
            approve_review(
                ch09_db,
                rid,
                ApproveReviewRequest(approved_answer="答案"),
                publish=lambda ids: pytest.fail("cannot publish before commit"),
                verify_published=None,
            )
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    with ch09_db() as db:
        assert db.get(ReviewQueue, rid).review_status == "待审"
        assert db.scalar(select(KnowledgeChunk)) is None
