from fastapi import APIRouter, HTTPException, Request

from mewhelp.ch05.api import RuntimeDep
from mewhelp.knowledge.refusals import PoolCommitError

from .feedback import (
    FeedbackReceipt,
    FeedbackRequest,
    recover_answer_snapshot,
    submit_negative_feedback,
)

router = APIRouter(prefix="/api/ch09", tags=["ch09"])


@router.post("/feedback", response_model=FeedbackReceipt)
async def feedback(body: FeedbackRequest, runtime: RuntimeDep, request: Request):
    async def checkpoints(session_id):
        async for state in runtime.graph.aget_state_history(
            {"configurable": {"thread_id": session_id}}, limit=500
        ):
            yield state

    async def recover(message_id):
        return await recover_answer_snapshot(
            message_id, factory=runtime.context.session_factory, checkpoint_reader=checkpoints
        )

    try:
        receipt = await submit_negative_feedback(
            runtime.context.session_factory, body, recover=recover
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="回答不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PoolCommitError as exc:
        raise HTTPException(status_code=503, detail="反馈暂未保存，请重试") from exc
    ch09 = getattr(request.app.state, "ch09_runtime", None)
    if ch09 and ch09.flywheel:
        ch09.flywheel.wake()
    return receipt
