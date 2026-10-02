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


@pytest.mark.parametrize("failure_stage", ["collection", "embedding", "upsert"])
def test_retry_publishes_committed_pending_policy_only(session_factory, tmp_path, failure_stage):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "shipping-policy.md").write_text("# 配送政策\n## 时效\n发货时效。", encoding="utf-8")
    with session_factory() as db:
        ingest_documents(db, docs)
        db.commit()
        unrelated = {row.id: {column.name: getattr(row, column.name)
                             for column in row.__table__.columns}
                     for row in db.scalars(select(KnowledgeChunk))}
    (docs / "aftersales-policy.md").write_text(
        "# 退款售后政策\n## 期限\n签收七天。\n## 条件\n须不影响二次销售。", encoding="utf-8")

    class FailingIndex(Index):
        failed = False
        writes = 0

        def ensure_collection(self):
            if failure_stage == "collection" and not self.failed:
                self.failed = True
                raise RuntimeError("publish interrupted")

        def upsert(self, row, vector):
            self.writes += 1
            if failure_stage == "upsert" and self.writes == 2 and not self.failed:
                self.failed = True
                raise RuntimeError("publish interrupted")
            super().upsert(row, vector)

    index = FailingIndex()

    def embed(texts):
        if failure_stage == "embedding" and not index.failed:
            index.failed = True
            raise RuntimeError("publish interrupted")
        return [[0] * 1024 for _ in texts]

    module = import_module("mewhelp.ch06.demo_publish")
    with pytest.raises(RuntimeError, match="publish interrupted"):
        module.prepare_demo_corpus(session_factory, index, docs, embed)
    with session_factory() as db:
        policy_ids = {row.id for row in db.scalars(select(KnowledgeChunk))
                      if "aftersales-policy.md" in row.section_path}
        assert len(policy_ids) >= 2
        assert any(db.get(KnowledgeChunk, row_id).vectorize_status == "pending"
                   for row_id in policy_ids)

    result = module.prepare_demo_corpus(session_factory, index, docs, embed)
    with session_factory() as db:
        for row_id in policy_ids:
            row = db.get(KnowledgeChunk, row_id)
            assert row.vectorize_status == "done" and row.vector_id == str(row_id)
        for row_id, expected in unrelated.items():
            row = db.get(KnowledgeChunk, row_id)
            assert {column.name: getattr(row, column.name)
                    for column in row.__table__.columns} == expected
    assert {row.id for row, _ in index.rows} == policy_ids
    assert result["new_policy_rows"] == 0 and result["published_rows"] == len(policy_ids)


def test_demo_publish_rejects_incomplete_policy_sync(session_factory, tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "aftersales-policy.md").write_text("# 退款售后政策\n## 期限\n签收七天。", encoding="utf-8")
    module = import_module("mewhelp.ch06.demo_publish")
    monkeypatch.setattr(module, "sync_pending", lambda *args, **kwargs: 0)
    with pytest.raises(ValueError, match="not fully published"):
        module.prepare_demo_corpus(session_factory, Index(), docs,
                                   lambda texts: [[0] * 1024 for _ in texts])
