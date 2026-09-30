"""知识录入 HTTP 边界；使用 SQLite 隔离本机 MySQL/Milvus。"""

from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mewhelp.db.base import Base
from mewhelp.main import app


def test_entry_is_readable_and_idempotent_when_vectorization_fails():
    from mewhelp.knowledge.api import KbRuntime, get_kb_runtime

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)

    def unavailable(_row_id: int) -> None:
        raise RuntimeError("Milvus unavailable")

    app.dependency_overrides[get_kb_runtime] = lambda: KbRuntime(lambda: Session(engine), unavailable)
    payload = {
        "entry_id": "a4275f49-e192-4f94-a849-ab1651e6ef40",
        "category": "物流",
        "questions": "邮费是多少",
        "answer": "单笔满 99 元包邮。",
        "section_path": "配送/运费",
        "content_type": "faq",
        "is_key_clause": False,
    }
    try:
        with TestClient(app) as client:
            first = client.post("/api/kb/entries", json=payload)
            second = client.post("/api/kb/entries", json=payload)
            listed = client.get("/api/kb/entries")
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    assert first.status_code == 200
    assert first.json()["vectorize_status"] == "pending"
    assert first.json()["sync_error"]
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert UUID(payload["entry_id"])
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["questions"] == "邮费是多少"
    assert listed.json()[0]["answer"] == "单笔满 99 元包邮。"
    assert listed.json()[0]["vectorize_status"] == "pending"


def test_retry_vectorizes_only_the_selected_pending_entry():
    from mewhelp.knowledge.api import KbRuntime, get_kb_runtime
    from mewhelp.knowledge.sync import sync_pending

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    state = {"available": False}
    upserts: list[int] = []

    class Vectors:
        def upsert(self, snapshot, _vector: list[float]) -> None:
            upserts.append(snapshot.id)

    def publish(row_id: int) -> None:
        if not state["available"]:
            raise RuntimeError("Milvus unavailable")
        sync_pending(
            lambda: Session(engine),
            lambda texts: [[0.1] * 1024 for _ in texts],
            Vectors(),
            row_ids=[row_id],
        )

    app.dependency_overrides[get_kb_runtime] = lambda: KbRuntime(lambda: Session(engine), publish)
    base = {
        "category": "物流",
        "answer": "满 99 元包邮。",
        "content_type": "faq",
    }
    try:
        with TestClient(app) as client:
            first = client.post("/api/kb/entries", json={
                **base, "entry_id": "f54ad852-71fc-458e-a948-06c9666ca642", "questions": "邮费多少"
            }).json()
            second = client.post("/api/kb/entries", json={
                **base, "entry_id": "f49c75eb-9d44-49af-bba9-975633680c40", "questions": "如何包邮"
            }).json()
            state["available"] = True
            retried = client.post(f"/api/kb/entries/{first['id']}/sync")
            listed = client.get("/api/kb/entries").json()
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    assert retried.status_code == 200
    assert retried.json()["vectorize_status"] == "done"
    assert upserts == [first["id"]]
    by_id = {row["id"]: row for row in listed}
    assert by_id[first["id"]]["vectorize_status"] == "done"
    assert by_id[second["id"]]["vectorize_status"] == "pending"


def test_reposting_completed_entry_does_not_claim_vectorization_failed():
    from mewhelp.knowledge.api import KbRuntime, get_kb_runtime
    from mewhelp.knowledge.sync import sync_pending

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    calls = 0

    class Vectors:
        def upsert(self, _row_id: int, _vector: list[float]) -> None:
            pass

    def publish(row_id: int) -> None:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("Milvus unavailable")
        sync_pending(
            lambda: Session(engine),
            lambda texts: [[0.1] * 1024 for _ in texts],
            Vectors(),
            row_ids=[row_id],
        )

    app.dependency_overrides[get_kb_runtime] = lambda: KbRuntime(lambda: Session(engine), publish)
    payload = {
        "entry_id": "80ba5001-b3cb-44be-a929-3f4f79cdf21d",
        "category": "物流",
        "questions": "包邮门槛是什么",
        "answer": "满 99 元包邮。",
    }
    try:
        with TestClient(app) as client:
            first = client.post("/api/kb/entries", json=payload)
            again = client.post("/api/kb/entries", json=payload)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    assert first.json()["vectorize_status"] == "done"
    assert again.status_code == 200
    assert again.json()["vectorize_status"] == "done"
    assert again.json()["sync_error"] is None
    assert calls == 1


def test_recent_entries_hide_document_deletion_markers():
    from mewhelp.knowledge.api import KbRuntime, get_kb_runtime
    from mewhelp.knowledge.store import KnowledgeDraft, put_chunk

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        put_chunk(session, KnowledgeDraft(
            "doc:retired", "配送", "旧运费说明", "已撤下。",
            "__deleting__::corpus:old/shipping.md", "policy",
        ))
        session.commit()
    app.dependency_overrides[get_kb_runtime] = lambda: KbRuntime(lambda: Session(engine), lambda _id: None)
    try:
        with TestClient(app) as client:
            response = client.get("/api/kb/entries")
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    assert response.status_code == 200
    assert response.json() == []


def test_save_keeps_pending_message_if_publisher_makes_no_progress():
    from mewhelp.knowledge.api import KbRuntime, get_kb_runtime

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    app.dependency_overrides[get_kb_runtime] = lambda: KbRuntime(lambda: Session(engine), lambda _id: None)
    try:
        with TestClient(app) as client:
            response = client.post("/api/kb/entries", json={
                "entry_id": "31c30cbe-948c-4be0-93f5-10651ff04e48",
                "category": "物流",
                "questions": "运费规则",
                "answer": "满 99 元包邮。",
            })
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    assert response.status_code == 200
    assert response.json()["vectorize_status"] == "pending"
    assert response.json()["sync_error"]
