import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from mewhelp.db.base import Base
from mewhelp.knowledge.store import KnowledgeDraft, put_chunk


@pytest.fixture
def cli_environment(monkeypatch):
    from mewhelp.db import engine as db
    from mewhelp.knowledge import embedding, vectors

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = lambda: Session(engine)
    monkeypatch.setattr(db, "SessionFactory", factory)
    monkeypatch.setattr(embedding, "embed_texts", lambda texts: [[0.1] * 1024 for _ in texts])
    with factory() as session:
        row = put_chunk(session, KnowledgeDraft("cli:one", "参数", "HX-210", "蓝牙 5.3", "手册", "manual"))
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
