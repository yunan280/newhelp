from collections.abc import AsyncIterable
from contextlib import aclosing
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from .runtime import WorkflowRuntime
from .schemas import TurnRequest, TurnResult
from .service import run_turn, stream_turn

router = APIRouter(prefix="/ch05", tags=["ch05"])


def get_workflow_runtime(request: Request) -> WorkflowRuntime:
    runtime = getattr(request.app.state, "ch05_runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="workflow runtime is not ready")
    return runtime


RuntimeDep = Annotated[WorkflowRuntime, Depends(get_workflow_runtime)]


@router.post("/agent")
async def agent(request: TurnRequest, runtime: RuntimeDep) -> TurnResult:
    try:
        return await run_turn(runtime, request)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"本轮处理失败：{type(exc).__name__}") from exc


@router.post("/chat/stream", response_class=EventSourceResponse)
async def chat(request: TurnRequest, runtime: RuntimeDep) -> AsyncIterable[ServerSentEvent]:
    try:
        async with aclosing(stream_turn(runtime, request, entry_point="chat_stream")) as stream:
            async for item in stream:
                yield ServerSentEvent(event=item["event"], data=item["data"])
    except Exception as exc:  # noqa: BLE001 -- errors after headers must use an SSE frame
        yield ServerSentEvent(
            event="error",
            data={"code": "workflow_error", "message": f"本轮处理失败：{type(exc).__name__}"},
        )
