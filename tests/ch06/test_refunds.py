from importlib import import_module

import pytest
from sqlalchemy import func, select

from mewhelp.ch05.schemas import RefundRequest, TurnRequest
from mewhelp.ch05.service import run_turn


def module():
    try:
        return import_module("mewhelp.ch06.refunds")
    except ImportError:
        pytest.fail("missing persisted refund application")


async def offered(runtime, question="订单1001能退吗", session="refund"):
    result = await run_turn(runtime, TurnRequest(message=question, session_id=session))
    assert result.refund_offer is not None, "eligible refund needs stable form offer"
    return result


def request(result, **changes):
    data = {"session_id": result.session_id, "offer_id": result.refund_offer.offer_id,
            "order_id": result.order.order_id, "reason": "七天无理由", "confirmed": True}
    data.update(changes)
    return RefundRequest(**data)


def count(factory):
    from mewhelp.db.models import RefundApplication
    with factory() as db:
        return db.scalar(select(func.count()).select_from(RefundApplication))


async def test_same_offer_exact_replay_is_one_persisted_application(core_runtime, session_factory):
    refunds = module()
    result = await offered(core_runtime)
    assert count(session_factory) == 0
    first = await refunds.submit_refund(core_runtime, request(result))
    replay = await refunds.submit_refund(core_runtime, request(result))
    assert first.application_no == replay.application_no and replay.replayed
    assert first.status == "pending" and not first.replayed and count(session_factory) == 1
    from mewhelp.db.models import RefundApplication
    with session_factory() as db:
        row = db.scalar(select(RefundApplication))
        assert row.order_snapshot["paid_amount"] == "199.00"
        assert row.assessment_snapshot["verdict"] == "eligible" and row.policy_snapshot
    with pytest.raises(refunds.RefundError) as changed:
        await refunds.submit_refund(core_runtime, request(result, reason="其他"))
    assert changed.value.status_code == 409 and count(session_factory) == 1


async def test_wrong_owner_order_and_stale_offer_do_not_write(core_runtime, core_factory, session_factory):
    refunds = module()
    result = await offered(core_runtime)
    for changes, status in [({"user_id":"bob"},403), ({"order_id":"1002"},409)]:
        with pytest.raises(refunds.RefundError) as error:
            await refunds.submit_refund(core_runtime, request(result, **changes))
        assert error.value.status_code == status
    core_factory.intents = ["物流"]
    await run_turn(core_runtime, TurnRequest(message="查订单1001物流", session_id=result.session_id))
    with pytest.raises(refunds.RefundError) as stale:
        await refunds.submit_refund(core_runtime, request(result))
    assert stale.value.status_code == 409 and count(session_factory) == 0


async def test_aftersales_has_no_refund_form(core_runtime, core_factory, session_factory):
    core_factory.intents = ["售后"]
    result = await run_turn(core_runtime, TurnRequest(message="订单1002想维修"))
    assert result.assessment.verdict == "eligible" and result.refund_offer is None
    assert count(session_factory) == 0


async def test_commit_then_checkpoint_failure_recovers_from_sql(core_runtime, session_factory, monkeypatch):
    refunds = module()
    result = await offered(core_runtime)
    async def unavailable(*args, **kwargs):
        raise OSError("checkpoint unavailable")
    monkeypatch.setattr(core_runtime.graph, "aupdate_state", unavailable)
    with pytest.raises(OSError):
        await refunds.submit_refund(core_runtime, request(result))
    assert count(session_factory) == 1
    monkeypatch.setattr(core_runtime.graph, "aget_state", unavailable)
    recovered = await refunds.submit_refund(core_runtime, request(result))
    assert recovered.replayed and count(session_factory) == 1
    receipt = refunds.read_refund_receipt(core_runtime.context, result.refund_offer.offer_id,
                                         result.session_id, "demo-user")
    assert receipt.application_no == recovered.application_no


async def test_offer_identity_is_stable_for_original_turn(core_runtime):
    refunds = module()
    result = await offered(core_runtime)
    state = await core_runtime.graph.aget_state({"configurable":{"thread_id":result.session_id}})
    assert refunds.create_refund_offer(state.values).offer_id == result.refund_offer.offer_id


def test_incomplete_assessment_cannot_create_form():
    refunds = module()
    assert refunds.create_refund_offer({"intent":"退款退货", "route":"aftersales",
                                      "stop_reason":"completed", "assessment":None}) is None
