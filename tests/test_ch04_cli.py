import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mewhelp.db.base import Base
from mewhelp.knowledge.refusals import LowConfidenceQuestion
from mewhelp.knowledge.store import KnowledgeDraft, put_chunk


@pytest.fixture
def cli_environment(monkeypatch):
    from mewhelp.db import engine as db
    from mewhelp.knowledge import embedding, vectors

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = lambda: Session(engine)
    monkeypatch.setattr(db, "SessionFactory", factory)
    monkeypatch.setattr(embedding, "embed_texts", lambda texts: [[0.1] * 1024 for _ in texts])
    with factory() as session:
        row = put_chunk(
            session, KnowledgeDraft("cli:one", "参数", "HX-210", "蓝牙 5.3", "手册", "manual")
        )
        row.vectorize_status = "done"
        row_id = row.id
        session.commit()

    class Index:
        def __init__(self):
            self.collection = None
            self.written = []
            self.differences = []

        def ensure_collection(self):
            pass

        def upsert(self, snapshot, vector):
            self.written.append(snapshot.id)

        def delete(self, ids):
            pass

        def audit(self, snapshots):
            assert [item.id for item in snapshots] == [row_id]
            return self.differences

    index = Index()

    def connect(settings, *, collection=None):
        index.collection = collection or settings.milvus_collection
        return index

    monkeypatch.setattr(vectors.MilvusSettings, "connect_hybrid", connect)
    return index, row_id


def test_reindex_targets_explicit_collection_and_previously_done_rows(cli_environment, capsys):
    from mewhelp.knowledge.cli import main

    index, row_id = cli_environment
    main(["reindex", "--collection", "knowledge_ch04"])
    assert index.collection == "knowledge_ch04"
    assert index.written == [row_id]
    assert "1" in capsys.readouterr().out


def test_audit_differences_are_machine_readable_and_fail_the_command(cli_environment, capsys):
    from mewhelp.knowledge.cli import main

    index, row_id = cli_environment
    index.differences = [{"id": str(row_id), "kind": "hash"}]
    with pytest.raises(SystemExit) as failed:
        main(["audit-index", "--collection", "knowledge_ch04"])
    assert failed.value.code == 1
    assert json.loads(capsys.readouterr().out) == index.differences


def test_audit_clean_index_succeeds(cli_environment, capsys):
    from mewhelp.knowledge.cli import main

    main(["audit-index", "--collection", "knowledge_ch04"])
    assert json.loads(capsys.readouterr().out) == []


def test_search_uses_shared_answering_and_cli_refusal_context(cli_environment, monkeypatch, capsys):
    from sqlalchemy import select

    from mewhelp.db import engine as db
    from mewhelp.knowledge import answering, query
    from mewhelp.knowledge.answering import RagRuntime
    from mewhelp.knowledge.cli import main
    from mewhelp.knowledge.query import QueryUnderstanding
    from mewhelp.knowledge.retrieval import RetrievalRuntime

    observed = []

    class Index:
        def search(self, strategy, **kwargs):
            observed.append((strategy, kwargs["filters"]))
            return []

    async def forbidden(messages):
        raise AssertionError("empty retrieval cannot generate")

    runtime = RagRuntime(
        RetrievalRuntime(db.SessionFactory, lambda t: [[0.1] * 1024], Index(), None),
        forbidden,
        db.SessionFactory,
        0.5,
        30000,
    )
    monkeypatch.setattr(answering, "get_rag_runtime", lambda *args, **kwargs: runtime)

    async def understand(question, **kwargs):
        return QueryUnderstanding(question, question, question, "knowledge", [])

    monkeypatch.setattr(query, "understand_query", understand)
    main(
        [
            "search",
            "--question",
            "HX-999有什么参数？",
            "--calibration",
            "unused-test.json",
            "--product-category",
            "耳机",
            "--category",
            "参数",
            "--content-type",
            "manual",
            "--is-key-clause",
            "false",
        ]
    )
    result = json.loads(capsys.readouterr().out)
    assert result["refused"] and result["sources"] == [] and result["low_confidence_question_id"]
    assert observed[0][0] == "hybrid_rerank"
    assert observed[0][1].model_dump() == {
        "category": "参数",
        "product_category": "耳机",
        "content_type": "manual",
        "is_key_clause": False,
    }
    with db.SessionFactory() as session:
        row = session.scalar(select(LowConfidenceQuestion))
        assert (
            row.original_question == "HX-999有什么参数？"
            and row.entry_point == "cli"
            and row.source_conversation_id is None
        )
