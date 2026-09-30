from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from mewhelp.db.base import Base
from mewhelp.knowledge.ingest import ingest_documents
from mewhelp.knowledge.store import KnowledgeChunk, pending_chunks


def test_repeated_headings_are_distinct_and_linked(tmp_path: Path):
    (tmp_path / "policy.md").write_text(
        "# 政策\n## 运费\n第一版。\n## 运费\n第二版。", encoding="utf-8"
    )
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert ingest_documents(session, tmp_path) == 2
        session.commit()
        rows = session.scalars(select(KnowledgeChunk).order_by(KnowledgeChunk.answer)).all()
        assert len({row.id for row in rows}) == 2
        assert rows[0].next_chunk_id == rows[1].id
        assert rows[1].prev_chunk_id == rows[0].id


def test_stale_document_remains_detectable_after_vector_delete_failure(tmp_path: Path):
    doc = tmp_path / "policy.md"
    doc.write_text("# 政策\n## 运费\n满 99 元包邮。", encoding="utf-8")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ingest_documents(session, tmp_path)
        session.commit()
        row_id = session.scalar(select(KnowledgeChunk.id))
        row = session.get(KnowledgeChunk, row_id)
        row.vectorize_status = "done"
        row.vector_id = str(row_id)
        session.commit()

    doc.unlink()
    first_attempt: list[int] = []
    with Session(engine) as session:
        assert ingest_documents(session, tmp_path, deleted_ids=first_attempt) == 0
        session.commit()
        assert first_attempt == [row_id]
        assert pending_chunks(session) == []
        row = session.get(KnowledgeChunk, row_id)
        assert row.section_path.startswith("__deleting__::")
        assert row.vectorize_status == "pending"
        assert row.vector_id is None

    # 模拟此时 Milvus 删除失败：MySQL tombstone 必须能在下次建库再次找到。
    second_attempt: list[int] = []
    with Session(engine) as session:
        ingest_documents(session, tmp_path, deleted_ids=second_attempt)
        assert second_attempt == [row_id]


def test_missing_document_root_cannot_clear_corpus(tmp_path: Path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session, pytest.raises(FileNotFoundError):
        ingest_documents(session, tmp_path / "misspelled")


def test_two_document_roots_with_same_filename_do_not_collide_or_clear_each_other(tmp_path: Path):
    roots = [tmp_path / "first", tmp_path / "second"]
    for index, root in enumerate(roots):
        root.mkdir()
        (root / "policy.md").write_text(
            f"# 政策\n## 运费\n第 {index} 版。", encoding="utf-8"
        )
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        ingest_documents(session, roots[0])
        ingest_documents(session, roots[1])
        session.commit()
        before = session.scalars(select(KnowledgeChunk).order_by(KnowledgeChunk.answer)).all()
        assert len({row.id for row in before}) == 2
        assert before[0].section_path != before[1].section_path

    (roots[1] / "policy.md").unlink()
    deleted: list[int] = []
    with Session(engine) as session:
        ingest_documents(session, roots[1], deleted_ids=deleted)
        session.commit()
        assert len(deleted) == 1
        assert session.get(KnowledgeChunk, before[0].id).section_path.startswith("corpus:")
