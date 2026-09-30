from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from mewhelp.db.base import Base
from mewhelp.knowledge import sync as knowledge_sync
from mewhelp.knowledge.store import KnowledgeChunk, KnowledgeDraft, put_chunk
from mewhelp.knowledge.sync import cleanup_deleting, sync_pending


class FakeVectors:
    def __init__(self):
        self.rows = {}
        self.fail_after_write = False

    def upsert(self, snapshot, vector):
        self.rows[snapshot.id] = (snapshot, vector)
        if self.fail_after_write:
            raise RuntimeError("中断在 Milvus 成功、MySQL 回填前")

    def delete(self, ids):
        if self.fail_after_write:
            raise RuntimeError("Milvus 删除失败")
        for row_id in ids:
            self.rows.pop(row_id, None)


def test_retries_both_failure_windows_without_duplicate_vectors():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = lambda: Session(engine)
    with factory() as session:
        row = put_chunk(session, KnowledgeDraft("faq:shipping", "物流", "运费怎么计算", "满 99 元包邮。", "", "faq"))
        row_id = row.id
        session.commit()
    vectors = FakeVectors()
    vectors.fail_after_write = True
    try:
        sync_pending(factory, lambda texts: [[0.1] * 1024 for _ in texts], vectors)
    except RuntimeError:
        pass
    with factory() as session:
        assert session.get(KnowledgeChunk, row_id).vectorize_status == "pending"
    vectors.fail_after_write = False
    assert sync_pending(factory, lambda texts: [[0.1] * 1024 for _ in texts], vectors) == 1
    assert list(vectors.rows) == [row_id]
    with factory() as session:
        row = session.scalar(select(KnowledgeChunk))
        assert row.vectorize_status == "done"
        assert row.vector_id == str(row_id)


def test_tombstone_cleanup_retries_after_milvus_delete_failure():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = lambda: Session(engine)
    with factory() as session:
        row = put_chunk(session, KnowledgeDraft("doc:old", "政策", "运费", "旧规则。", "doc::运费", "policy"))
        row_id = row.id
        row.section_path = "__deleting__::doc::运费"
        session.commit()
    vectors = FakeVectors()
    vectors.rows[row_id] = [0.1] * 1024
    vectors.fail_after_write = True
    try:
        cleanup_deleting(factory, vectors)
    except RuntimeError:
        pass
    with factory() as session:
        assert session.get(KnowledgeChunk, row_id) is not None
    vectors.fail_after_write = False
    assert cleanup_deleting(factory, vectors) == 1
    assert vectors.rows == {}
    with factory() as session:
        assert session.get(KnowledgeChunk, row_id) is None


def test_metadata_change_while_embedding_cannot_be_marked_done():
    from dataclasses import replace

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = lambda: Session(engine)
    draft = KnowledgeDraft("race:metadata", "参数", "HX-210", "支持蓝牙", "手册", "manual")
    with factory() as session:
        row_id = put_chunk(session, draft).id
        session.commit()

    def embed(texts):
        with factory() as session:
            put_chunk(session, replace(draft, product_category="耳机"))
            session.commit()
        return [[0.1] * 1024 for _ in texts]

    assert sync_pending(factory, embed, FakeVectors()) == 0
    with factory() as session:
        row = session.get(KnowledgeChunk, row_id)
        assert row.vectorize_status == "pending" and row.vector_id is None


def test_full_reindex_includes_done_rows_and_excludes_tombstones():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = lambda: Session(engine)
    with factory() as session:
        pending = put_chunk(session, KnowledgeDraft("full:p", "参数", "p", "p", "", "faq"))
        done = put_chunk(session, KnowledgeDraft("full:d", "参数", "d", "d", "", "faq"))
        dead = put_chunk(session, KnowledgeDraft("full:x", "参数", "x", "x", "__deleting__::x", "faq"))
        done.vectorize_status = "done"
        ids, dead_id = {pending.id, done.id}, dead.id
        session.commit()
    writer = FakeVectors()
    assert knowledge_sync.reindex_all(factory, lambda texts: [[0.1] * 1024 for _ in texts], writer, batch_size=1) == 2
    assert set(writer.rows) == ids and dead_id not in writer.rows
