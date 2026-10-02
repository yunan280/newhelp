from collections.abc import AsyncIterable
from contextlib import aclosing

from fastapi import APIRouter, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent

from mewhelp.ch05.api import RuntimeDep
from mewhelp.ch05.schemas import OrderResumeRequest, TurnResult

from .selection import SelectionError, pending_selection, resume_order, stream_order_resume

router = APIRouter(prefix="/ch06", tags=["ch06"])


@router.get("/sessions/{session_id}/pending")
async def pending(session_id: str, runtime: RuntimeDep, user_id: str = "demo-user"):
    try:
        return await pending_selection(runtime, session_id, user_id)
    except SelectionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/orders/selection")
async def select_order(request: OrderResumeRequest, runtime: RuntimeDep) -> TurnResult:
    try:
        return await resume_order(runtime, request)
    except SelectionError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="订单流程未完成，请使用同一订单选择重试") from exc


@router.post("/orders/selection/stream", response_class=EventSourceResponse)
async def select_order_stream(request: OrderResumeRequest, runtime: RuntimeDep) -> AsyncIterable[ServerSentEvent]:
    try:
        async with aclosing(stream_order_resume(runtime, request)) as stream:
            async for item in stream:
                yield ServerSentEvent(event=item["event"], data=item["data"])
    except SelectionError as exc:
        yield ServerSentEvent(event="error", data={"code": "selection_error", "status": exc.status_code,
                                                   "message": str(exc)})
    except Exception:  # noqa: BLE001 - failures after stream headers need explicit error frames
        yield ServerSentEvent(event="error", data={"code": "resume_error",
                                                   "message": "订单流程未完成，请使用同一订单选择重试"})
