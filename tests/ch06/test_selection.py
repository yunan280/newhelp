import asyncio
import time

import pytest

from mewhelp.ch05.schemas import OrderResumeRequest, TurnRequest
from mewhelp.ch05.service import run_turn


async def waiting(runtime, session="select"):
    result = await run_turn(runtime, TurnRequest(message="这个能退吗", session_id=session))
    assert result.status == "waiting_for_order" and result.order_selection.orders
    assert not runtime.locks.lock(session).locked()
    return result


def request(result, order="1001", **kwargs):
    return OrderResumeRequest(session_id=result.session_id,
                             selection_id=result.order_selection.selection_id,
                             order_id=order, **kwargs)


async def test_resume_restarts_only_wait_node(selection_runtime):
    from mewhelp.ch06.selection import resume_order
    before = await waiting(selection_runtime)
    after = await resume_order(selection_runtime, request(before))
    assert after.status == "completed" and after.order.order_id == "1001"
    assert after.calls["classifier"] == before.calls["classifier"]
    assert after.usage.total >= before.usage.total
    replay = await resume_order(selection_runtime, request(before))
    assert replay == after


async def test_stale_card_and_wrong_owner_rejected(selection_runtime):
    from mewhelp.ch06.selection import SelectionError, resume_order
    before = await waiting(selection_runtime)
    with pytest.raises(SelectionError) as wrong:
        await resume_order(selection_runtime, request(before, user_id="bob"))
    assert wrong.value.status_code == 403
    await resume_order(selection_runtime, request(before))
    with pytest.raises(SelectionError) as changed:
        await resume_order(selection_runtime, request(before, "1002"))
    assert changed.value.status_code == 409
    with pytest.raises(SelectionError) as stale:
        await resume_order(selection_runtime, request(before).model_copy(update={"selection_id": "old"}))
    assert stale.value.status_code == 409


async def test_new_message_cancels_pending_selection(selection_runtime):
    from mewhelp.ch06.selection import SelectionError, resume_order
    before = await waiting(selection_runtime)
    after = await asyncio.wait_for(run_turn(selection_runtime, TurnRequest(
        message="不退了，查物流", session_id=before.session_id,
    )), 3)
    assert after.intent == "物流" and after.status == "completed"
    with pytest.raises(SelectionError) as stale:
        await resume_order(selection_runtime, request(before))
    assert stale.value.status_code == 409


async def test_resume_after_181_seconds_keeps_usage(selection_runtime, monkeypatch):
    from mewhelp.ch06 import selection
    before = await waiting(selection_runtime)
    now = time.time()
    monkeypatch.setattr(selection.time, "time", lambda: now + 181)
    after = await selection.resume_order(selection_runtime, request(before))
    assert after.usage.total >= before.usage.total and after.order.order_id == "1001"
    state = await selection_runtime.graph.aget_state({"configurable": {"thread_id": before.session_id}})
    assert state.values["started_at"] == now + 181


def test_error_checkpoint_cannot_be_mistaken_for_waiting():
    from types import SimpleNamespace

    from mewhelp.ch06.selection import active_selection
    selection = {"selection_id": "s", "turn_id": "t", "orders": []}
    snapshot = SimpleNamespace(values={"selection_status": "pending", "order_selection": selection},
                               tasks=[SimpleNamespace(error="RuntimeError",
                                      interrupts=[SimpleNamespace(value={"kind": "order_selection", **selection})])])
    assert active_selection(snapshot) is None


async def test_failed_resume_retries_same_choice_with_fresh_window(selection_runtime, monkeypatch):
    from mewhelp.ch06 import selection
    before = await waiting(selection_runtime)
    selection_runtime.test_controls.fail_once = True
    with pytest.raises(RuntimeError, match="transient"):
        await selection.resume_order(selection_runtime, request(before))
    now = time.time()
    monkeypatch.setattr(selection.time, "time", lambda: now + 181)
    after = await selection.resume_order(selection_runtime, request(before))
    assert after.order.order_id == "1001" and after.calls == before.calls
