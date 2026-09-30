"""MySQL→BGE-M3→Milvus 补偿同步；两次提交之间可安全重跑。"""

from collections.abc import Callable
from typing import Protocol

from sqlalchemy import LargeBinary, and_, cast, select, update
from sqlalchemy.orm import Session

from .store import ChunkSnapshot, KnowledgeChunk, pending_chunks, snapshot_chunk


class VectorWriter(Protocol):
    def upsert(self, snapshot: ChunkSnapshot, vector: list[float]) -> None: ...


class VectorDeleter(Protocol):
    def delete(self, ids: list[int]) -> None: ...


def cleanup_deleting(session_factory: Callable[[], Session], vectors: VectorDeleter) -> int:
    """先删除 Milvus 向量，再清除 MySQL tombstone；失败时留待下次重跑。"""
    with session_factory() as session:
        ids = list(session.scalars(select(KnowledgeChunk.id).where(
            KnowledgeChunk.section_path.startswith("__deleting__::", autoescape=True)
        )))
    if not ids:
        return 0
    vectors.delete(ids)
    with session_factory() as session:
        for row_id in ids:
            row = session.get(KnowledgeChunk, row_id)
            if row is not None and (row.section_path or "").startswith("__deleting__::"):
                session.delete(row)
        session.commit()
    return len(ids)


def sync_pending(
    session_factory: Callable[[], Session],
    embed: Callable[[list[str]], list[list[float]]],
    vectors: VectorWriter,
    *,
    limit: int = 100,
    row_ids: list[int] | None = None,
) -> int:
    """先读已提交原文，再向量化；每块独立回填，失败保留待处理状态。"""
    with session_factory() as session:
        snapshots = [
            snapshot_chunk(row)
            for row in pending_chunks(session, limit, row_ids=row_ids)
        ]
    if not snapshots:
        return 0
    return _publish_snapshots(session_factory, embed, vectors, snapshots)


def _publish_snapshots(
    session_factory: Callable[[], Session],
    embed: Callable[[list[str]], list[list[float]]],
    vectors: VectorWriter,
    snapshots: list[ChunkSnapshot],
) -> int:
    if not snapshots:
        return 0
    encoded = embed([item.text for item in snapshots])
    if len(encoded) != len(snapshots):
        raise ValueError("BGE-M3 返回的向量数量与知识块数量不一致")
    completed = 0
    for snapshot, vector in zip(snapshots, encoded, strict=True):
        if len(vector) != 1024:
            raise ValueError(f"BGE-M3 dense 维度应为 1024，实际为 {len(vector)}")
        vectors.upsert(snapshot, vector)
        with session_factory() as session:
            criteria = []
            for name in ("category", "questions", "answer", "section_path", "product_category", "content_type", "is_key_clause"):
                column, value = getattr(KnowledgeChunk, name), getattr(snapshot, name)
                # MySQL's default text collation is case insensitive. Hash comparisons are not.
                if isinstance(value, str):
                    column, value = cast(column, LargeBinary), value.encode("utf-8")
                criteria.append(column.is_not_distinct_from(value))
            matches = and_(*criteria)
            applied = session.execute(update(KnowledgeChunk).where(
                KnowledgeChunk.id == snapshot.id, matches,
            ).values(vector_id=str(snapshot.id), vectorize_status="done").execution_options(synchronize_session=False))
            if applied.rowcount:
                completed += 1
            else:
                # An older worker may have overwritten a newer index write. Force compensation.
                session.execute(update(KnowledgeChunk).where(
                    KnowledgeChunk.id == snapshot.id, ~matches,
                ).values(vector_id=None, vectorize_status="pending").execution_options(synchronize_session=False))
            session.commit()
    return completed


def reindex_all(
    session_factory: Callable[[], Session],
    embed: Callable[[list[str]], list[list[float]]],
    index: VectorWriter,
    *, batch_size: int = 100,
) -> int:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    last_id, completed = -1, 0
    while True:
        with session_factory() as session:
            rows = session.scalars(select(KnowledgeChunk).where(
                KnowledgeChunk.id > last_id,
                (KnowledgeChunk.section_path.is_(None))
                | ~KnowledgeChunk.section_path.startswith("__deleting__::", autoescape=True),
            ).order_by(KnowledgeChunk.id).limit(batch_size)).all()
            snapshots = [snapshot_chunk(row) for row in rows]
        if not snapshots:
            break
        completed += _publish_snapshots(session_factory, embed, index, snapshots)
        last_id = snapshots[-1].id
    return completed
