from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from mewhelp.db.base import Base
from mewhelp.knowledge.store import (
    KnowledgeChunk,
    KnowledgeDraft,
    pending_chunks,
    put_chunk,
    source_id,
)


def _engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def test_same_source_key_is_idempotent_and_changed_answer_becomes_pending():
    engine = _engine()
    draft = KnowledgeDraft("faq:1", "物流", "运费怎么计算", "满 99 元包邮。", "", "faq")
    with Session(engine) as session:
        first = put_chunk(session, draft)
        session.commit()
        first_id = first.id
    with Session(engine) as session:
        again = put_chunk(session, draft)
        assert again.id == first_id
        again.vectorize_status = "done"
        again.vector_id = str(first_id)
        session.commit()
    with Session(engine) as session:
        changed = put_chunk(session, KnowledgeDraft("faq:1", "物流", "运费怎么计算", "满 88 元包邮。", "", "faq"))
        assert changed.id == first_id
        assert changed.vectorize_status == "pending"
        assert changed.vector_id is None
        assert len(session.scalars(select(KnowledgeChunk)).all()) == 1


def test_pending_scan_recovers_after_mysql_commit():
    engine = _engine()
    with Session(engine) as session:
        put_chunk(session, KnowledgeDraft("doc:a:0", "政策", "运费", "满 99 元包邮。", "政策 / 运费", "document"))
        session.commit()
    with Session(engine) as session:
        assert [row.id for row in pending_chunks(session)] == [source_id("doc:a:0")]
