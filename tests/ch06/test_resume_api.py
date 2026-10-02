import httpx

from tests.ch06.test_selection import request, waiting


async def test_json_sse_and_readonly_pending(selection_runtime):
    from mewhelp.ch05.api import get_workflow_runtime
    from mewhelp.main import app
    app.dependency_overrides[get_workflow_runtime] = lambda: selection_runtime
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for mode in ["json", "stream"]:
                before = await waiting(selection_runtime, mode)
                pending = await client.get(f"/ch06/sessions/{mode}/pending")
                assert pending.status_code == 200 and pending.json()["status"] == "waiting_for_order"
                denied = await client.post("/ch06/orders/selection", json=request(before, user_id="bob").model_dump())
                assert denied.status_code == 403
                url = "/ch06/orders/selection" + ("/stream" if mode == "stream" else "")
                response = await client.post(url, json=request(before).model_dump())
                assert response.status_code == 200
                if mode == "json":
                    assert response.json()["order"]["order_id"] == "1001"
                else:
                    assert "event: done" in response.text and "event: error" not in response.text
            missing = await client.get("/ch06/sessions/never-seen/pending")
            assert missing.status_code == 200 and missing.json() is None
    finally:
        app.dependency_overrides.pop(get_workflow_runtime, None)
