"""Approval is durable before publication; every retry uses the same source key."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select

from mewhelp.db.models import ReviewQueue
from mewhelp.knowledge.refusals import LowConfidenceQuestion, utc_now
from mewhelp.knowledge.store import (
    KnowledgeChunk,
    KnowledgeDraft,
    put_chunk,
    snapshot_chunk,
    source_id,
)


class ApproveReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    approved_answer: str = Field(min_length=1, max_length=16000)
    category: str = Field(default="客服补充FAQ", min_length=1, max_length=255)
    product_category: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("approved_answer", "category", "product_category")
    @classmethod
    def nonblank(cls, value):
        if value is None:
            return None
        if not value.strip():
            raise ValueError("核准内容不能为空白")
        return value.strip()


class ReviewPublication(BaseModel):
    review_id: str
    review_status: Literal["待审", "通过", "驳回"]
    knowledge_id: str | None = None
    publication_status: str | None = None
    sync_error: str | None = None


def knowledge_id(review_id):
    return source_id(f"ch09-review:{review_id}")


def locked_review(db, review_id):
    row = db.scalar(
        select(ReviewQueue)
        .where(ReviewQueue.id == review_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise LookupError("待审问题不存在")
    return row


def approve_review(factory, review_id, request, *, publish, verify_published):
    with factory.begin() as db:
        row = locked_review(db, review_id)
        if row.review_status == "驳回":
            raise ValueError("已驳回问题不可核准")
        kid = knowledge_id(review_id)
        if row.review_status == "通过":
            saved = db.get(KnowledgeChunk, kid)
            if (
                saved is None
                or row.approved_answer != request.approved_answer
                or saved.answer != request.approved_answer
                or saved.category != request.category
                or saved.product_category != request.product_category
                or saved.questions != row.normalized_question
            ):
                raise ValueError("已核准答案或分类与请求不一致")
        else:
            if db.get(KnowledgeChunk, kid) is not None:
                raise ValueError("核准知识源键已被占用")
            draft = KnowledgeDraft(
                source_key=f"ch09-review:{review_id}",
                category=request.category,
                product_category=request.product_category,
                questions=row.normalized_question,
                answer=request.approved_answer,
                section_path=f"客服补充FAQ/ch09-review:{review_id}",
                content_type="faq",
            )
            put_chunk(db, draft)
            row.approved_answer = request.approved_answer
            row.review_status = "通过"
            row.updated_at = utc_now()
    return retry_publication(factory, review_id, publish=publish, verify_published=verify_published)


def reject_review(factory, review_id):
    with factory.begin() as db:
        row = locked_review(db, review_id)
        if row.review_status == "通过":
            raise ValueError("已核准问题不能驳回")
        row.review_status = "驳回"
        row.updated_at = utc_now()
    return ReviewPublication(review_id=str(review_id), review_status="驳回")


def retry_publication(factory, review_id, *, publish, verify_published):
    kid = knowledge_id(review_id)
    with factory() as db:
        review = db.get(ReviewQueue, review_id)
        if review is None:
            raise LookupError("待审问题不存在")
        row = db.get(KnowledgeChunk, kid)
        if review.review_status != "通过" or row is None:
            raise ValueError("只有已核准问题可以发布")
        should_publish = row.vectorize_status != "done" or row.vector_id != str(kid)
    error = None
    try:
        if should_publish:
            publish([kid])
        with factory() as db:
            row = db.get(KnowledgeChunk, kid)
            done = row is not None and row.vectorize_status == "done" and row.vector_id == str(kid)
        if not done or not verify_published(kid):
            raise RuntimeError("核准原文已保存，当前Milvus尚未确认可见")
    except Exception as exc:  # noqa: BLE001 — approval remains committed; compensate any publication failure
        error = f"{type(exc).__name__}: {exc}"[:512]
        with factory.begin() as db:
            row = db.get(KnowledgeChunk, kid)
            if row is not None:
                row.vectorize_status = "pending"
                row.vector_id = None
    return ReviewPublication(
        review_id=str(review_id),
        review_status="通过",
        knowledge_id=str(kid),
        publication_status="pending" if error else "published",
        sync_error=error,
    )


def verify_chunk_visible(factory, index, row_id):
    with factory() as db:
        row = db.get(KnowledgeChunk, row_id)
        if row is None or row.vectorize_status != "done" or row.vector_id != str(row_id):
            return False
        snapshot = snapshot_chunk(row)
    hits = index.client.query(
        collection_name=index.collection,
        ids=[row_id],
        output_fields=["id", "content_hash"],
        consistency_level="Strong",
        timeout=10,
    )
    return (
        len(hits) == 1
        and int(hits[0]["id"]) == row_id
        and hits[0]["content_hash"] == snapshot.content_hash
    )


def review_json(row, db):
    knowledge = (
        db.get(KnowledgeChunk, knowledge_id(row.id)) if row.review_status == "通过" else None
    )
    return {
        "id": str(row.id),
        "normalized_question": row.normalized_question,
        "ai_suggested_answer": row.ai_suggested_answer,
        "occurrence_count": row.occurrence_count,
        "review_status": row.review_status,
        "approved_answer": row.approved_answer,
        "knowledge_id": str(knowledge.id) if knowledge else None,
        "publication_status": ("indexed" if knowledge.vectorize_status == "done" else "pending")
        if knowledge
        else None,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "category": knowledge.category if knowledge else None,
        "product_category": knowledge.product_category if knowledge else None,
    }


def pagination(page, page_size):
    if page < 1 or not 1 <= page_size <= 100:
        raise ValueError("分页参数无效")


def list_reviews(factory, *, status="待审", page=1, page_size=20, sort="occurrence"):
    pagination(page, page_size)
    if status not in {"待审", "通过", "驳回", "全部"} or sort not in {"occurrence", "created"}:
        raise ValueError("审核筛选或排序无效")
    with factory() as db:
        predicate = ReviewQueue.review_status == status if status != "全部" else True
        total = db.scalar(select(func.count()).select_from(ReviewQueue).where(predicate))
        ordering = (
            (ReviewQueue.occurrence_count.desc(), ReviewQueue.id.asc())
            if sort == "occurrence"
            else (ReviewQueue.id.desc(),)
        )
        rows = db.scalars(
            select(ReviewQueue)
            .where(predicate)
            .order_by(*ordering)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return {
            "items": [review_json(row, db) for row in rows],
            "total": total,
            "page": page,
            "page_size": page_size,
        }


def review_detail(factory, review_id, *, page=1, page_size=20):
    pagination(page, page_size)
    with factory() as db:
        review = db.get(ReviewQueue, review_id)
        if review is None:
            raise LookupError("待审问题不存在")
        predicate = LowConfidenceQuestion.matched_review_id == review_id
        total = db.scalar(select(func.count()).select_from(LowConfidenceQuestion).where(predicate))
        rows = db.scalars(
            select(LowConfidenceQuestion)
            .where(predicate)
            .order_by(LowConfidenceQuestion.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        originals = [
            {
                "id": str(row.id),
                "original_question": row.original_question,
                "entry_point": row.entry_point,
                "trigger_stage": row.trigger_stage,
                "reason_code": row.reason_code,
                "reason": row.reason,
                "retrieved_chunks": row.retrieved_chunks,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
        return {
            **review_json(review, db),
            "originals": {"items": originals, "total": total, "page": page, "page_size": page_size},
        }
