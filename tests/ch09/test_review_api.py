from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from sqlalchemy import select

from mewhelp.db.models import ReviewQueue
from mewhelp.knowledge.store import KnowledgeChunk


async def test_review_http_approval_conflict_and_details(ch09_db, monkeypatch):
    from mewhelp.ch09 import api

    with ch09_db.begin() as db:
        row = ReviewQueue(normalized_question="问题？")
        db.add(row)
        db.flush()
        rid = row.id

    def publish(ids):
        with ch09_db.begin() as db:
            row = db.get(KnowledgeChunk, ids[0])
            row.vectorize_status = "done"
            row.vector_id = str(row.id)

    monkeypatch.setattr(
        api, "publication_callbacks", lambda runtime: (publish, lambda i: True), raising=False
    )
    app = FastAPI()
    app.include_router(api.router)
    app.state.ch05_runtime = SimpleNamespace(context=SimpleNamespace(session_factory=ch09_db))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        listing = await client.get("/api/ch09/reviews")
        detail = await client.get(f"/api/ch09/reviews/{rid}")
        invalid = await client.post(
            f"/api/ch09/reviews/{rid}/approve", json={"approved_answer": "  "}
        )
        approved = await client.post(
            f"/api/ch09/reviews/{rid}/approve", json={"approved_answer": "核准答案"}
        )
        conflict = await client.post(
            f"/api/ch09/reviews/{rid}/approve", json={"approved_answer": "改变答案"}
        )
        rejected = await client.post(f"/api/ch09/reviews/{rid}/reject")
        bad_page = await client.get("/api/ch09/reviews?page=0")
    assert listing.status_code == detail.status_code == approved.status_code == 200
    assert approved.json()["publication_status"] == "published"
    assert invalid.status_code == bad_page.status_code == 422
    assert conflict.status_code == rejected.status_code == 409
    with ch09_db() as db:
        assert len(db.scalars(select(KnowledgeChunk)).all()) == 1
