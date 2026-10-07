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
