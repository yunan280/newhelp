from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from mewhelp.db.base import Base
from mewhelp.db.models import Conversation, Message, MsgRole
from mewhelp.knowledge.mining import ExtractedQa, mine_conversations, publish_batch
from mewhelp.knowledge.store import KnowledgeChunk, QaStaging


def test_batch_stages_then_deduplicates_and_rerun_keeps_same_knowledge_id():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = lambda: Session(engine)
    with factory() as session:
        session.add(Conversation(session_id="c1", user_id="u1"))
        session.flush()
        cid = session.scalar(select(Conversation.id))
        session.add(Message(conversation_id=cid, role=MsgRole.user, content="邮费是多少"))
        session.add(Message(conversation_id=cid, role=MsgRole.assistant, content="满 99 元包邮。"))
        session.commit()

    def extract(samples):
        assert samples[0].conversation_id == cid
        return [
            ExtractedQa(source_conversation_id=cid, question="邮费是多少", answer="满 99 元包邮。"),
            ExtractedQa(source_conversation_id=cid, question="邮费是多少？", answer="满 99 元包邮。"),
        ]

    assert mine_conversations(factory, extract, batch_size=1) == 1
    assert mine_conversations(factory, extract, batch_size=1) == 0
    with factory() as session:
        assert len(session.scalars(select(QaStaging)).all()) == 3
        assert len(session.scalars(select(KnowledgeChunk)).all()) == 1


def test_empty_extraction_is_marked_processed_and_orphan_staging_is_retried():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = lambda: Session(engine)
    with factory() as session:
        session.add(Conversation(session_id="c2", user_id="u2"))
        session.flush()
        cid = session.scalar(select(Conversation.id))
        session.add(Message(conversation_id=cid, role=MsgRole.user, content="你好"))
        session.add(QaStaging(batch_no="crashed", source_ref=f"{cid}:1", question="半截", answer="未发布", status="extracted"))
        session.commit()
    calls = []

    def extract(samples):
        calls.append(samples)
        return []

    assert mine_conversations(factory, extract) == 0
    assert mine_conversations(factory, extract) == 0
    assert len(calls) == 1
    with factory() as session:
        rows = session.scalars(select(QaStaging)).all()
        assert len(rows) == 1
        assert rows[0].status == "discarded"


def test_conflicting_answers_and_private_data_stay_out_of_knowledge():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            QaStaging(batch_no="b", source_ref="1", question="运费是多少", answer="8 元。", status="extracted"),
            QaStaging(batch_no="b", source_ref="2", question="运费是多少", answer="12 元。", status="extracted"),
            QaStaging(batch_no="b", source_ref="3", question="电话是多少", answer="13812345678", status="extracted"),
        ])
        session.flush()
        assert publish_batch(session, "b") == 0
        assert session.scalars(select(KnowledgeChunk)).all() == []
        assert {r.status for r in session.scalars(select(QaStaging)).all()} == {"discarded"}


def test_later_batch_cannot_overwrite_a_published_answer():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(QaStaging(batch_no="first", source_ref="1:2", question="邮费是多少", answer="满 99 元包邮。", status="extracted"))
        session.flush()
        assert publish_batch(session, "first") == 1
        session.add(QaStaging(batch_no="second", source_ref="2:4", question="邮费是多少？", answer="满 88 元包邮。", status="extracted"))
        session.flush()
        assert publish_batch(session, "second") == 0
        assert session.scalar(select(KnowledgeChunk.answer)) == "满 99 元包邮。"
