import json

import httpx
import pytest


@pytest.fixture
def app(workflow_runtime):
    from mewhelp.ch05.api import get_workflow_runtime
    from mewhelp.main import app

    app.dependency_overrides[get_workflow_runtime] = lambda: workflow_runtime
    yield app
    app.dependency_overrides.pop(get_workflow_runtime, None)


async def test_json_and_sse_share_final_contract(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        js = await client.post("/ch05/agent", json={"message": "你好"})
        stream = await client.post("/ch05/chat/stream", json={"message": "你好"})
    assert js.status_code == stream.status_code == 200
    frames = [f for f in stream.text.replace("\r\n", "\n").split("\n\n") if "event:" in f]
    assert "event: session" in frames[0] and "event: done" in frames[-1]
    done = json.loads(
        next(line[6:] for line in frames[-1].splitlines() if line.startswith("data: "))
    )
    assert js.json()["answer"] == done["answer"]
    assert js.json()["calls"] == done["calls"]


async def test_classifier_failure_has_error_without_done(app, model_factory):
    model_factory.fail_classifier = True
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        stream = await client.post("/ch05/chat/stream", json={"message": "查询失败"})
        js = await client.post("/ch05/agent", json={"message": "查询失败"})
    assert "event: error" in stream.text and "event: done" not in stream.text
    assert js.status_code == 502


async def test_blank_message_keeps_existing_validation(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/ch05/agent", json={"message": " "})
    assert response.status_code == 422
