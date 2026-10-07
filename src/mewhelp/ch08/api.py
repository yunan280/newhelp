from collections.abc import AsyncIterable
from contextlib import aclosing

from fastapi import APIRouter, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent

from mewhelp.ch05.api import RuntimeDep
from mewhelp.ch05.schemas import TurnResult

from .confirmation import (
    TicketConfirmationError,
    pending_ticket,
    resume_ticket,
    stream_ticket_resume,
)
from .schemas import TicketResumeRequest

router = APIRouter(prefix='/ch08', tags=['ch08'])


@router.post('/tickets/resume')
async def tickets_resume(request: TicketResumeRequest, runtime: RuntimeDep) -> TurnResult:
    try:
        return await resume_ticket(runtime, request)
    except TicketConfirmationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post('/tickets/resume/stream', response_class=EventSourceResponse)
async def tickets_resume_stream(request: TicketResumeRequest, runtime: RuntimeDep) -> AsyncIterable[ServerSentEvent]:
    try:
        async with aclosing(stream_ticket_resume(runtime, request)) as stream:
            async for item in stream:
                yield ServerSentEvent(event=item['event'], data=item['data'])
    except Exception as exc:  # noqa: BLE001 — SSE必须把未知故障回传客户端
        yield ServerSentEvent(event='error', data={'code': 'ticket_confirmation_error', 'message': str(exc)})


@router.get('/sessions/{session_id}/pending')
async def tickets_pending(session_id: str, runtime: RuntimeDep, user_id: str = 'demo-user') -> TurnResult | None:
    try:
        return await pending_ticket(runtime, session_id, user_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
