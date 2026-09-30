"""Ch04 metadata invalidation, original-question pool and citation transactions."""

import datetime as dt
from dataclasses import replace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from mewhelp.db.base import Base
from mewhelp.db.models import Conversation, Message, MsgRole
from mewhelp.db.repository import TurnMessage, append_messages
from mewhelp.knowledge import store
from mewhelp.knowledge.ingest import ingest_documents


@pytest.fixture
def factory():
    engine = create_engine("sqlite://")
    # Register the new table when present, without making absence a collection error.
    try:
        import mewhelp.knowledge.refusals  # noqa: F401
    except ModuleNotFoundError:
        pass
    Base.metadata.create_all(engine)
    return lambda: Session(engine)


def draft(**changes):
    value = store.KnowledgeDraft("ch04:one", "退款", "退款规则？", "需原支付渠道退款。", "政策 / 退款", "policy")
    return replace(value, **changes)


@pytest.mark.parametrize("change", [
    {"product_category": "耳机"},
    {"content_type": "manual"},
    {"is_key_clause": True},
    {"section_path": "政策 / 售后退款"},
])
def test_metadata_only_change_invalidates_published_snapshot(factory, change):
    with factory() as session:
        row = store.put_chunk(session, draft())
        row.vectorize_status, row.vector_id = "done", str(row.id)
        session.commit()
        original_id = row.id
        before = store.snapshot_chunk(row)
        changed = store.put_chunk(session, draft(**change))
        assert changed.id == original_id
        assert changed.vectorize_status == "pending" and changed.vector_id is None
        assert store.snapshot_chunk(changed).content_hash != before.content_hash


def test_unchanged_chunk_stays_done_and_snapshot_is_detached(factory):
    with factory() as session:
        row = store.put_chunk(session, draft(product_category="耳机"))
        row.vectorize_status, row.vector_id = "done", str(row.id)
        session.commit()
        snapshot = store.snapshot_chunk(row)
        row = store.put_chunk(session, draft(product_category="耳机"))
        assert row.vectorize_status == "done"
        assert store.snapshot_chunk(row) == snapshot
    assert snapshot.product_category == "耳机"
    assert snapshot.text == "分类：退款\n问题：退款规则？\n答案：需原支付渠道退款。"
    assert len(snapshot.content_hash) == 64


def test_legacy_product_category_is_null(factory):
    with factory() as session:
        row = store.put_chunk(session, draft())
        assert row.product_category is None and row.category == "退款"


def test_document_header_populates_category_without_polluting_body(factory, tmp_path):
    (tmp_path / "headphone-manual.md").write_text(
        "<!-- product-category: 耳机 -->\n# HX-210\n支持蓝牙 5.3。", encoding="utf-8"
    )
    with factory() as session:
        ingest_documents(session, tmp_path)
        session.commit()
        rows = session.scalars(select(store.KnowledgeChunk)).all()
        assert rows and all(row.product_category == "耳机" for row in rows)
        assert all("product-category" not in store.embedding_text(row) for row in rows)
        ids = {row.id for row in rows}
        ingest_documents(session, tmp_path)
        assert {row.id for row in session.scalars(select(store.KnowledgeChunk))} == ids


def test_undeclared_document_category_is_not_guessed(factory, tmp_path):
    (tmp_path / "headphones.md").write_text("# 耳机\n售后需凭购买记录。", encoding="utf-8")
    with factory() as session:
        ingest_documents(session, tmp_path)
        assert all(row.product_category is None for row in session.scalars(select(store.KnowledgeChunk)))


def test_refusal_commits_original_question_independently(factory):
    from mewhelp.knowledge.refusals import LowConfidenceQuestion, RefusalInput, record_refusal

    with factory() as session:
        conversation = Conversation(session_id="ch04-pool", user_id="u1")
        session.add(conversation)
        session.commit()
        conversation_id = conversation.id
    before = dt.datetime.now(dt.UTC)
    pool_id = record_refusal(factory, RefusalInput(
        original_question="HX-210 没到账，今天肯定能到吗？",
        source_conversation_id=conversation_id, entry_point="chat_stream",
        trigger_stage="generation", reason_code="insufficient_evidence", reason="无法承诺到账时间",
    ))
    # Rolling back the final-message transaction must not roll back this record.
    with factory() as session:
        append_messages(session, conversation_id=conversation_id, rows=[TurnMessage(MsgRole.assistant, "拒答")])
        session.rollback()
    with factory() as session:
        saved = session.get(LowConfidenceQuestion, int(pool_id))
        assert saved.original_question == "HX-210 没到账，今天肯定能到吗？"
        assert saved.source_conversation_id == conversation_id
        assert saved.entry_point == "chat_stream" and saved.trigger_stage == "generation"
        assert saved.reason_code == "insufficient_evidence"
        assert before <= saved.created_at.replace(tzinfo=dt.UTC) <= dt.datetime.now(dt.UTC)
        assert not session.scalars(select(Message)).all()


def test_cli_refusal_allows_no_conversation(factory):
    from mewhelp.knowledge.refusals import LowConfidenceQuestion, RefusalInput, record_refusal

    pool_id = record_refusal(factory, RefusalInput("缺失问题", None, "cli", "retrieval", "no_evidence", "无知识"))
    with factory() as session:
        assert session.get(LowConfidenceQuestion, int(pool_id)).source_conversation_id is None


def test_online_refusal_requires_conversation(factory):
    from mewhelp.knowledge.refusals import RefusalInput, record_refusal

    with pytest.raises(ValueError, match="conversation"):
        record_refusal(factory, RefusalInput("缺失问题", None, "agent", "retrieval", "no_evidence", "无知识"))


def test_pool_commit_error_remains_observable(factory):
    from mewhelp.knowledge.refusals import PoolCommitError, RefusalInput, record_refusal

    class BrokenSession(Session):
        def commit(self):
            raise RuntimeError("disk failure")

    with factory() as session:
        engine = session.get_bind()
    with pytest.raises(PoolCommitError) as caught:
        record_refusal(lambda: BrokenSession(engine), RefusalInput("问题", None, "cli", "retrieval", "no_evidence", "无知识"))
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_citations_and_answer_commit_together(factory):
    source = {"number": 1, "chunk_id": "9007199254740993", "answer": "原文"}
    with factory() as session:
        conv = Conversation(session_id="citations", user_id="u1")
        session.add(conv)
        session.flush()
        append_messages(session, conversation_id=conv.id, rows=[
            TurnMessage(MsgRole.assistant, "答案[1]", citations=[source]),
        ])
        session.commit()
    with factory() as session:
        final = session.scalar(select(Message))
        assert final.content == "答案[1]"
        assert final.citations == [source]


def test_citation_transaction_rollback_leaves_no_answer(factory):
    with factory() as session:
        conv = Conversation(session_id="citation-rollback", user_id="u1")
        session.add(conv)
        session.flush()
        append_messages(session, conversation_id=conv.id, rows=[
            TurnMessage(MsgRole.assistant, "答案[1]", citations=[{"number": 1, "chunk_id": "1"}]),
        ])
        session.rollback()
    with factory() as session:
        assert not session.scalars(select(Message)).all()
