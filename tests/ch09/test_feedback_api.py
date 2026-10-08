from types import SimpleNamespace

import httpx
from fastapi import FastAPI
from feedback_helpers import seed_answer

from mewhelp.ch09.api import router


async def test_http_feedback_replay_and_owner_validation(generation_context):
    ctx, cid = generation_context
    seed_answer(ctx.session_factory, cid)
    app = FastAPI()
    app.include_router(router)
    app.state.ch05_runtime = SimpleNamespace(context=ctx)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        body = {"user_id": "u", "session_id": "s", "answer_message_id": "11", "choice": "down"}
        first = await client.post("/api/ch09/feedback", json=body)
        replay = await client.post("/api/ch09/feedback", json=body)
        foreign = await client.post("/api/ch09/feedback", json={**body, "user_id": "other"})
        missing = await client.post("/api/ch09/feedback", json={**body, "answer_message_id": "999"})
        invalid = await client.post("/api/ch09/feedback", json={**body, "answer_message_id": 11})
    assert first.status_code == replay.status_code == 200
    assert first.json()["pool_id"] == replay.json()["pool_id"] and replay.json()["replayed"]
    assert foreign.status_code == missing.status_code == 404 and foreign.json() == missing.json()
    assert invalid.status_code == 422


async def test_flywheel_diagnostics_and_retry_do_not_create_another_pool(generation_context):
    from sqlalchemy import select

    from mewhelp.ch09.flywheel import FlywheelWorker
    from mewhelp.knowledge.refusals import LowConfidenceQuestion

    ctx, cid = generation_context
    seed_answer(ctx.session_factory, cid)
    app = FastAPI()
    app.include_router(router)
    app.state.ch05_runtime = SimpleNamespace(context=ctx)
    worker = FlywheelWorker(ctx.session_factory, normalize=None, dedup=None)
    app.state.ch09_runtime = SimpleNamespace(flywheel=worker)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        receipt = await client.post(
            "/api/ch09/feedback",
            json={"user_id": "u", "session_id": "s", "answer_message_id": "11", "choice": "down"},
        )
        pid = receipt.json()["pool_id"]
        status = await client.get("/api/ch09/flywheel/status")
        retried = await client.post("/api/ch09/flywheel/retry/" + pid)
        missing = await client.post("/api/ch09/flywheel/retry/999")
    assert status.json()["pending_count"] == 1 and retried.json()["scheduled"]
    assert missing.status_code == 404
    with ctx.session_factory() as db:
        assert len(db.scalars(select(LowConfidenceQuestion)).all()) == 1
