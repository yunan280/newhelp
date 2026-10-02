import httpx
import pytest

from tests.ch06.test_refunds import count, offered, request


@pytest.fixture
def refund_app(core_runtime):
    from mewhelp.ch05.api import get_workflow_runtime
    from mewhelp.main import app
    app.dependency_overrides[get_workflow_runtime] = lambda: core_runtime
    yield app
    app.dependency_overrides.pop(get_workflow_runtime, None)


async def test_invalid_confirmation_reason_or_client_amount_never_writes(refund_app, core_runtime, session_factory):
    result = await offered(core_runtime)
    payload = request(result).model_dump()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=refund_app), base_url="http://test") as client:
        for patch in [{"confirmed":False}, {"confirmed":1}, {"reason":""},
                      {"reason":"随便退款"}, {"paid_amount":"1.00"}]:
            response = await client.post("/ch06/refunds", json={**payload, **patch})
            assert response.status_code == 422
        denied = await client.post("/ch06/refunds", json={**payload, "user_id":"bob"})
        assert denied.status_code == 403
    assert count(session_factory) == 0


async def test_submit_read_receipt_and_replay(refund_app, core_runtime, session_factory):
    result = await offered(core_runtime)
    payload = request(result).model_dump()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=refund_app), base_url="http://test") as client:
        first = await client.post("/ch06/refunds", json=payload)
        assert first.status_code == 200 and first.json()["status"] == "pending"
        receipt = await client.get("/ch06/refunds/"+payload["offer_id"], params={"session_id":result.session_id})
        assert receipt.status_code == 200 and receipt.json()["application_no"] == first.json()["application_no"]
        denied = await client.get("/ch06/refunds/"+payload["offer_id"], params={"session_id":result.session_id,"user_id":"bob"})
        assert denied.status_code == 403
        replay = await client.post("/ch06/refunds", json=payload)
        assert replay.json()["replayed"] and replay.json()["application_no"] == first.json()["application_no"]
    assert count(session_factory) == 1


async def test_chat_sse_delivers_server_order_and_stable_refund_offer(refund_app):
    from scripts.smoke_ch05_acceptance import completed_sse
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=refund_app), base_url="http://test") as client:
        response = await client.post("/ch05/chat/stream", json={"message":"订单1001能退吗", "session_id":"refund-sse"})
    result, frames = completed_sse(response.text)
    assert result["refund_offer"]["order"]["paid_amount"] == "199.00"
    assert len(result["refund_offer"]["offer_id"]) == 64
    assert [f["event"] for f in frames].index("sources") < [f["event"] for f in frames].index("token")
