import asyncio
import threading

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from mewhelp.ch05.schemas import TurnRequest
from mewhelp.db.models import Conversation, ConvStatus, Ticket


def module():
    try:
        from mewhelp.ch05 import actions
        from mewhelp.ch05.schemas import TicketRequest
    except ImportError:
        pytest.fail("confirmed ticket boundary missing")
    return actions, TicketRequest


async def offer(runtime, model_factory):
    from mewhelp.ch05.service import run_turn

    model_factory.intents = ["投诉"]
    result = await run_turn(runtime, TurnRequest(message="我要投诉", session_id="actions"))
    return result.offer


def test_confirmation_must_be_boolean_true():
    _, request = module()
    for confirmation in [False, 1, "true", None]:
        with pytest.raises(ValidationError):
            request(
                session_id="s",
                offer_id="x",
                confirmed=confirmation,
                description="投诉",
                ticket_type="投诉",
            )


async def test_only_confirmed_offer_writes_and_replays_same_ticket(
    workflow_runtime, model_factory, session_factory
):
    m, request = module()
    suggested = await offer(workflow_runtime, model_factory)
    req = request(
        session_id="actions",
        offer_id=suggested.offer_id,
        confirmed=True,
        description="客服态度问题",
        ticket_type="投诉",
    )
    first = await m.create_confirmed_ticket(workflow_runtime, req)
    second = await m.create_confirmed_ticket(workflow_runtime, req)
    assert first.ticket_no == second.ticket_no and not first.replayed and second.replayed
    with session_factory() as db:
        assert len(db.scalars(select(Ticket)).all()) == 1
        assert db.scalar(select(Conversation)).status is ConvStatus.ongoing
    with pytest.raises(m.ActionError) as conflict:
        await m.create_confirmed_ticket(
            workflow_runtime, req.model_copy(update={"description": "改了"})
        )
    assert conflict.value.status_code == 409


@pytest.mark.parametrize(
    "session_id,user_id,offer_id",
    [
        ("actions", None, "forged"),
        ("other", None, "use_real"),
        ("actions", "wrong-user", "use_real"),
    ],
)
async def test_forged_or_cross_session_offers_cannot_write(
    workflow_runtime, model_factory, session_factory, session_id, user_id, offer_id
):
    m, request = module()
    suggested = await offer(workflow_runtime, model_factory)
    req = request(
        session_id=session_id,
        user_id=user_id,
        offer_id=suggested.offer_id if offer_id == "use_real" else offer_id,
        confirmed=True,
        description="问题",
        ticket_type="投诉",
    )
    with pytest.raises(m.ActionError):
        await m.create_confirmed_ticket(workflow_runtime, req)
    with session_factory() as db:
        assert db.scalars(select(Ticket)).all() == []


async def test_checkpoint_ack_failure_then_retry_cannot_duplicate(
    workflow_runtime, model_factory, session_factory, monkeypatch
):
    m, request = module()
    suggested = await offer(workflow_runtime, model_factory)
    req = request(
        session_id="actions",
        offer_id=suggested.offer_id,
        confirmed=True,
        description="投诉",
        ticket_type="投诉",
    )
    original = workflow_runtime.graph.aupdate_state

    async def broken(*args, **kwargs):
        raise RuntimeError("checkpoint ack failed")

    monkeypatch.setattr(workflow_runtime.graph, "aupdate_state", broken)
    with pytest.raises(RuntimeError):
        await m.create_confirmed_ticket(workflow_runtime, req)
    monkeypatch.setattr(workflow_runtime.graph, "aupdate_state", original)
    replay = await m.create_confirmed_ticket(workflow_runtime, req)
    assert replay.replayed
    with session_factory() as db:
        assert len(db.scalars(select(Ticket)).all()) == 1


async def test_two_confirm_requests_write_once(workflow_runtime, model_factory, session_factory):
    m, request = module()
    suggested = await offer(workflow_runtime, model_factory)
    req = request(
        session_id="actions",
        offer_id=suggested.offer_id,
        confirmed=True,
        description="投诉",
        ticket_type="投诉",
    )
    results = await asyncio.gather(
        *(m.create_confirmed_ticket(workflow_runtime, req) for _ in range(2))
    )
    assert results[0].ticket_no == results[1].ticket_no
    with session_factory() as db:
        assert len(db.scalars(select(Ticket)).all()) == 1


async def test_database_unique_key_handles_two_tool_writers(session_factory, monkeypatch):
    from mewhelp.tools import ticket

    with session_factory() as db:
        conv = Conversation(session_id="two-writers", user_id="demo-user")
        db.add(conv)
        db.commit()
        cid = conv.id
    barrier = threading.Barrier(2, timeout=5)
    count_lock = threading.Lock()
    calls = 0
    original = ticket.next_ticket_no

    def same_number(db, *, day):
        nonlocal calls
        no = original(db, day=day)
        with count_lock:
            calls += 1
            wait = calls <= 2
        if wait:
            barrier.wait()
        return no

    monkeypatch.setattr(ticket, "next_ticket_no", same_number)
    tools = [
        ticket.build_ticket_tools(session_factory, cid, request_id="stable-key")[0]
        for _ in range(2)
    ]
    receipts = await asyncio.gather(
        *(t.ainvoke({"description": "同一确认", "ticket_type": "投诉"}) for t in tools)
    )
    with session_factory() as db:
        row = db.scalars(select(Ticket)).one()
        assert row.ticket_no in receipts[0] and row.ticket_no in receipts[1]
