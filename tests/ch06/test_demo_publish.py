from importlib import import_module

import pytest
from sqlalchemy import select

from mewhelp.knowledge.ingest import ingest_documents
from mewhelp.knowledge.store import KnowledgeChunk, snapshot_chunk


class Index:
    collection = "ch06_eval_mysql_test"

    def __init__(self):
        self.rows = []

    def ensure_collection(self):
        pass

    def upsert(self, row, vector):
        self.rows.append((row, vector))

    def audit(self, snapshots):
        return []


def test_changed_existing_document_rolls_back_before_index_publish(session_factory, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    policy = docs / "aftersales-policy.md"
    policy.write_text("# 政策\n## 原条款\n原始政策事实。", encoding="utf-8")
    with session_factory() as db:
        ingest_documents(db, docs)
        db.commit()
        before = [(row.id, snapshot_chunk(row).content_hash) for row in db.scalars(select(KnowledgeChunk))]
    policy.write_text("# 政策\n## 原条款\n不应覆盖已有事实。", encoding="utf-8")
    index = Index()
    module = import_module("mewhelp.ch06.demo_publish")
    with pytest.raises(ValueError, match="existing"):
        module.prepare_demo_corpus(session_factory, index, docs, lambda rows: [[0] * 1024] * len(rows))
    with session_factory() as db:
        assert [(row.id, snapshot_chunk(row).content_hash) for row in db.scalars(select(KnowledgeChunk))] == before
    assert index.rows == []


def test_demo_publisher_refuses_default_collection(session_factory, tmp_path):
    index = Index()
    index.collection = "mewhelp_qa"
    module = import_module("mewhelp.ch06.demo_publish")
    with pytest.raises(ValueError, match="separate"):
        module.prepare_demo_corpus(session_factory, index, tmp_path, lambda rows: [])
    assert index.rows == []


def test_demo_publisher_ingests_only_approved_aftersales_source(session_factory, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "aftersales-policy.md").write_text("# 退款售后政策\n## 期限\n签收七天。", encoding="utf-8")
    (docs / "shipping-policy.md").write_text("# 配送政策\n## 时效\n发货时效。", encoding="utf-8")
    result = import_module("mewhelp.ch06.demo_publish").prepare_demo_corpus(
        session_factory, Index(), docs, lambda rows: [[0] * 1024] * len(rows))
    with session_factory() as db:
        rows = db.scalars(select(KnowledgeChunk)).all()
        assert rows and all("aftersales-policy.md" in row.section_path for row in rows)
    assert result["new_policy_rows"] == len(rows)
