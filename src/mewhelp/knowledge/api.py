"""人工录入知识的 HTTP 边界；原文先提交，向量失败时保留待补偿行。"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .answering import source_dtos
from .retrieval import RankedChunk, RetrievalResult
from .sources import DocumentSource, read_document_source, read_published_chunk
from .store import KnowledgeChunk, KnowledgeDraft, put_chunk

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/kb", tags=["knowledge"])


class KnowledgeSourceSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )
    knowledge_docs_root: Path = Path("knowledge-docs")


@dataclass(frozen=True)
class KbRuntime:
    session_factory: Callable[[], Session]
    publish: Callable[[int], None]
    docs_root: Path = field(default_factory=lambda: KnowledgeSourceSettings().knowledge_docs_root)


def get_kb_runtime() -> KbRuntime:
    from mewhelp.db.engine import SessionFactory

    def publish(row_id: int) -> None:
        from .embedding import embed_texts
        from .sync import sync_pending
        from .vectors import MilvusSettings

        vectors = MilvusSettings().connect_hybrid()
        vectors.ensure_collection()
        sync_pending(SessionFactory, embed_texts, vectors, row_ids=[row_id])

    return KbRuntime(SessionFactory, publish)


RuntimeDep = Annotated[KbRuntime, Depends(get_kb_runtime)]


class KnowledgeEntryIn(BaseModel):
    entry_id: UUID
    category: str = Field(min_length=1, max_length=255)
    product_category: str | None = Field(default=None, min_length=1, max_length=128)
    questions: str = Field(min_length=1, max_length=16000)
    answer: str = Field(min_length=1, max_length=16000)
    section_path: str | None = Field(default=None, max_length=512)
    content_type: Literal["faq", "policy", "manual"] = "faq"
    is_key_clause: bool = False

    @field_validator("category", "questions", "answer", "product_category")
    @classmethod
    def nonempty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("不能只填空白字符")
        return value


def _as_json(row: KnowledgeChunk) -> dict:
    return {
        "id": row.id,
        "category": row.category,
        "product_category": row.product_category,
        "questions": row.questions,
        "answer": row.answer,
        "section_path": row.section_path,
        "content_type": row.content_type,
        "is_key_clause": bool(row.is_key_clause),
        "vectorize_status": row.vectorize_status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _entry_response(runtime: KbRuntime, row_id: int, *, publish: bool) -> dict:
    sync_error = None
    if publish:
        try:
            runtime.publish(row_id)
        except Exception:  # 原文已提交，失败留待补偿
            logger.exception("知识 %s 向量化失败，保留 pending", row_id)
            sync_error = "原文已保存，向量化暂不可用；请稍后重试"

    with runtime.session_factory() as session:
        row = session.get(KnowledgeChunk, row_id)
        if row is None:
            raise HTTPException(status_code=404, detail="知识条目不存在")
        result = _as_json(row)
    if result["vectorize_status"] == "pending" and sync_error is None:
        sync_error = "原文已保存，仍待向量化；请稍后重试"
    return {**result, "sync_error": sync_error}


@router.get("/entries")
def list_entries(runtime: RuntimeDep) -> list[dict]:
    with runtime.session_factory() as session:
        rows = session.scalars(
            select(KnowledgeChunk)
            .where(or_(
                KnowledgeChunk.section_path.is_(None),
                ~KnowledgeChunk.section_path.startswith("__deleting__::", autoescape=True),
            ))
            .order_by(KnowledgeChunk.created_at.desc(), KnowledgeChunk.id.desc())
            .limit(50)
        ).all()
        return [_as_json(row) for row in rows]


@router.post("/entries")
def create_entry(req: KnowledgeEntryIn, runtime: RuntimeDep) -> dict:
    with runtime.session_factory() as session:
        row = put_chunk(
            session,
            KnowledgeDraft(
                source_key=f"manual-entry:{req.entry_id}",
                category=req.category,
                product_category=req.product_category,
                questions=req.questions,
                answer=req.answer,
                section_path=req.section_path.strip() or None if req.section_path else None,
                content_type=req.content_type,
                is_key_clause=req.is_key_clause,
            ),
        )
        row_id = row.id
        should_publish = row.vectorize_status == "pending"
        session.commit()

    return _entry_response(runtime, row_id, publish=should_publish)


@router.post("/entries/{row_id}/sync")
def retry_entry(row_id: int, runtime: RuntimeDep) -> dict:
    with runtime.session_factory() as session:
        row = session.get(KnowledgeChunk, row_id)
        if row is None or (row.section_path or "").startswith("__deleting__::"):
            raise HTTPException(status_code=404, detail="知识条目不存在")
        should_publish = row.vectorize_status == "pending"
    return _entry_response(runtime, row_id, publish=should_publish)


@router.get("/chunks/{chunk_id}")
def read_chunk(chunk_id: int, runtime: RuntimeDep) -> dict:
    chunk = read_published_chunk(runtime.session_factory, chunk_id)
    if chunk is None:
        raise HTTPException(status_code=404, detail="来源不存在或尚未发布")
    source = source_dtos(RetrievalResult([chunk], [RankedChunk(chunk, None)]))[0]
    return source.model_dump(exclude={"number"})


@router.get("/chunks/{chunk_id}/document", response_model=DocumentSource)
def read_document(chunk_id: int, runtime: RuntimeDep) -> DocumentSource:
    chunk = read_published_chunk(runtime.session_factory, chunk_id)
    document = read_document_source(chunk, runtime.docs_root) if chunk else None
    if document is None:
        raise HTTPException(status_code=404, detail="来源文档不存在")
    return document
