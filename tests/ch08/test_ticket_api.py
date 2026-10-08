import httpx
from fastapi import FastAPI

from .test_ticket_graph import install_tools


async def test_preview_pending_and_resume_stream_http(workflow_runtime, tmp_path, session_factory, model_factory):
    from mewhelp.ch05.api import router as chat_router
    from mewhelp.ch08.api import router

    from .test_ticket_graph import CONTROL, ticket_call
    await install_tools(workflow_runtime, tmp_path, session_factory)
    app = FastAPI()
    app.state.ch05_runtime = workflow_runtime
    app.include_router(chat_router)
    app.include_router(router)
    model_factory.decisions = [ticket_call(), CONTROL]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/ch05/chat/stream', json={'message': '键盘坏了，帮我建售后工单', 'session_id': 'http-ticket'})
        assert 'event: ticket_preview' in response.text
        assert 'event: waiting_for_ticket' in response.text
        pending = (await client.get('/ch08/sessions/http-ticket/pending')).json()
        confirmation_id = pending['ticket_preview']['confirmation_id']
        response = await client.post('/ch08/tickets/resume/stream', json={'session_id': 'http-ticket', 'confirmation_id': confirmation_id, 'action': 'confirm'})
        assert 'event: ticket_receipt' in response.text
        assert 'event: done' in response.text
        assert 'event: error' not in response.text


async def test_readonly_receipt_recovers_lost_response_without_reexecuting(workflow_runtime, tmp_path, session_factory, model_factory):
    from sqlalchemy import func, select

    from mewhelp.ch08.api import router
    from mewhelp.ch08.confirmation import resume_ticket
    from mewhelp.ch08.schemas import TicketResumeRequest
    from mewhelp.db.models import Ticket

    from .test_ticket_graph import preview
    await install_tools(workflow_runtime, tmp_path, session_factory)
    waiting = await preview(workflow_runtime, model_factory, session='lost-response')
    nonce = waiting.ticket_preview['confirmation_id']
    app = FastAPI()
    app.state.ch05_runtime = workflow_runtime
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        url = f'/ch08/sessions/lost-response/receipts/{nonce}'
        assert (await client.get(url)).json() is None  # A GET cannot confirm a pending write.
        with session_factory() as db:
            assert db.scalar(select(func.count()).select_from(Ticket)) == 0
        result = await resume_ticket(workflow_runtime, TicketResumeRequest(session_id='lost-response', confirmation_id=nonce, action='confirm'))
        recovered = await client.get(url)
        assert recovered.status_code == 200
        assert recovered.json()['ticket_receipt']['ticket_no'] == result.ticket_receipt['ticket_no']
        assert (await client.get(url, params={'user_id': 'mallory'})).status_code == 403
        with session_factory() as db:
            assert db.scalar(select(func.count()).select_from(Ticket)) == 1


async def test_stream_stale_card_error_carries_http_status(workflow_runtime):
    from mewhelp.ch08.api import router
    app = FastAPI()
    app.state.ch05_runtime = workflow_runtime
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/ch08/tickets/resume/stream', json={'session_id':'no-session', 'confirmation_id':'old', 'action':'confirm'})
        assert '"status":404' in response.text.replace(' ', '')
