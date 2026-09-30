"""用户 ch03 DDL 的 ORM 镜像；确定性 BIGINT 主键使双写可重跑。"""

import datetime as dt
import hashlib
import json
from dataclasses import dataclass

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text, or_, select
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, Session, mapped_column

from mewhelp.db.base import Base

_PK = mysql.BIGINT(unsigned=True).with_variant(Integer, "sqlite")


def source_id(source_key: str) -> int:
    """源位置映射到稳定正整数，显式写入自增主键；不增加权威 DDL 的列。"""
    value = int.from_bytes(hashlib.sha256(source_key.encode("utf-8")).digest()[:8], "big")
    return value & ((1 << 63) - 1) or 1


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        Index("idx_category", "category").ddl_if(dialect="mysql"),
        Index("idx_vectorize_status", "vectorize_status"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(255), nullable=False)
    product_category: Mapped[str | None] = mapped_column(String(128))
    questions: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    section_path: Mapped[str | None] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(32))
    is_key_clause: Mapped[bool] = mapped_column(mysql.TINYINT(1).with_variant(Integer, "sqlite"), nullable=False, default=0)
    prev_chunk_id: Mapped[int | None] = mapped_column(_PK, ForeignKey("knowledge_chunks.id", name="fk_chunks_prev", ondelete="SET NULL"))
    next_chunk_id: Mapped[int | None] = mapped_column(_PK, ForeignKey("knowledge_chunks.id", name="fk_chunks_next", ondelete="SET NULL"))
    vector_id: Mapped[str | None] = mapped_column(String(64))
    vectorize_status: Mapped[str] = mapped_column(Enum("pending", "done"), nullable=False, default="pending")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False, default=dt.datetime.now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False, default=dt.datetime.now, onupdate=dt.datetime.now)


class QaStaging(Base):
    __tablename__ = "qa_extraction_staging"
    __table_args__ = (
        Index("idx_batch_no", "batch_no"),
        Index("idx_status", "status"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    batch_no: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(255))
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Enum("extracted", "kept", "discarded"), nullable=False, default="extracted")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False, default=dt.datetime.now)


@dataclass(frozen=True)
class KnowledgeDraft:
    source_key: str
    category: str
    questions: str
    answer: str
    section_path: str | None
    content_type: str | None
    is_key_clause: bool = False
    prev_key: str | None = None
    next_key: str | None = None
    product_category: str | None = None


@dataclass(frozen=True)
class ChunkSnapshot:
    id: int
    text: str
    questions: str
    answer: str
    section_path: str | None
    category: str
    product_category: str | None
    content_type: str | None
    is_key_clause: bool
    content_hash: str


def embedding_text(draft: KnowledgeDraft | KnowledgeChunk) -> str:
    return f"分类：{draft.category}\n问题：{draft.questions}\n答案：{draft.answer}"


def _content_hash(value: KnowledgeDraft | KnowledgeChunk) -> str:
    payload = {
        "version": 1,
        "category": value.category,
        "questions": value.questions,
        "answer": value.answer,
        "product_category": value.product_category,
        "content_type": value.content_type,
        "is_key_clause": bool(value.is_key_clause),
        "section_path": value.section_path,
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def snapshot_chunk(row: KnowledgeChunk) -> ChunkSnapshot:
    return ChunkSnapshot(
        id=row.id, text=embedding_text(row), questions=row.questions, answer=row.answer,
        section_path=row.section_path, category=row.category, product_category=row.product_category,
        content_type=row.content_type, is_key_clause=bool(row.is_key_clause),
        content_hash=_content_hash(row),
    )


def put_chunk(session: Session, draft: KnowledgeDraft) -> KnowledgeChunk:
    if draft.product_category is not None and (
        not draft.product_category.strip() or len(draft.product_category) > 128
    ):
        raise ValueError("product_category must contain 1–128 characters or be None")
    row = session.get(KnowledgeChunk, source_id(draft.source_key))
    if row is None:
        row = KnowledgeChunk(id=source_id(draft.source_key), vectorize_status="pending")
        session.add(row)
    elif _content_hash(row) != _content_hash(draft):
        row.vectorize_status = "pending"
        row.vector_id = None
    row.category = draft.category
    row.product_category = draft.product_category
    row.questions = draft.questions
    row.answer = draft.answer
    row.section_path = draft.section_path
    row.content_type = draft.content_type
    row.is_key_clause = draft.is_key_clause
    session.flush()
    return row


def pending_chunks(
    session: Session, limit: int = 100, *, row_ids: list[int] | None = None
) -> list[KnowledgeChunk]:
    query = (
        select(KnowledgeChunk)
        .where(
            KnowledgeChunk.vectorize_status == "pending",
            or_(
                KnowledgeChunk.section_path.is_(None),
                ~KnowledgeChunk.section_path.startswith("__deleting__::", autoescape=True),
            ),
        )
    )
    if row_ids is not None:
        query = query.where(KnowledgeChunk.id.in_(row_ids))
    return list(session.scalars(query.order_by(KnowledgeChunk.id).limit(limit)))
