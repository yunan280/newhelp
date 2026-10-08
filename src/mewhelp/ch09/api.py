import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request
from sqlalchemy.exc import SQLAlchemyError

from mewhelp.ch05.api import RuntimeDep
from mewhelp.knowledge.refusals import PoolCommitError

from .evaluation_jobs import EvaluationJob, EvaluationRequest
from .feedback import (
    FeedbackReceipt,
    FeedbackRequest,
    recover_answer_snapshot,
    submit_negative_feedback,
)
from .reviews import ApproveReviewRequest, ReviewPublication

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


def flywheel_for(request):
    ch09 = getattr(request.app.state, "ch09_runtime", None)
    if not ch09 or not ch09.flywheel:
        raise HTTPException(status_code=503, detail="飞轮工作器尚未启用")
    return ch09.flywheel


@router.get("/flywheel/status")
async def flywheel_status(request: Request):
    return await asyncio.to_thread(flywheel_for(request).status)


@router.post("/flywheel/retry/{pool_id}")
async def flywheel_retry(pool_id: int, request: Request):
    from .flywheel import read_gap

    if not 0 < pool_id < 2**64:
        raise HTTPException(status_code=422, detail="问题池ID无效")
    worker = flywheel_for(request)
    try:
        _, _, matched = await asyncio.to_thread(read_gap, worker.factory, pool_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if matched:
        return {"pool_id": str(pool_id), "review_id": str(matched), "scheduled": False}
    worker.retry(pool_id)
    return {"pool_id": str(pool_id), "scheduled": True}


ReviewId = Annotated[int, Path(gt=0, lt=2**64)]
Page = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


def review_boundary(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail="数据库暂不可用，请重试") from exc


def publication_callbacks(runtime):
    from mewhelp.knowledge.sync import sync_pending

    from .reviews import verify_chunk_visible

    # Milvus/model setup runs only after the approval transaction commits.
    def publish(ids):
        retrieval = runtime.context.rag_factory().retrieval
        sync_pending(runtime.context.session_factory, retrieval.embed, retrieval.index, row_ids=ids)

    def verify(row_id):
        retrieval = runtime.context.rag_factory().retrieval
        return verify_chunk_visible(runtime.context.session_factory, retrieval.index, row_id)

    return publish, verify


@router.get("/reviews")
def reviews(
    runtime: RuntimeDep,
    status: Literal["待审", "通过", "驳回", "全部"] = "待审",
    page: Page = 1,
    page_size: PageSize = 20,
    sort: Literal["occurrence", "created"] = "occurrence",
):
    from .reviews import list_reviews

    return review_boundary(
        list_reviews,
        runtime.context.session_factory,
        status=status,
        page=page,
        page_size=page_size,
        sort=sort,
    )


@router.get("/reviews/{review_id}")
def review_read(review_id: ReviewId, runtime: RuntimeDep, page: Page = 1, page_size: PageSize = 20):
    from .reviews import review_detail

    return review_boundary(
        review_detail, runtime.context.session_factory, review_id, page=page, page_size=page_size
    )


@router.post("/reviews/{review_id}/approve", response_model=ReviewPublication)
def review_approve(review_id: ReviewId, body: ApproveReviewRequest, runtime: RuntimeDep):
    from .reviews import approve_review

    publish, verify = publication_callbacks(runtime)
    return review_boundary(
        approve_review,
        runtime.context.session_factory,
        review_id,
        body,
        publish=publish,
        verify_published=verify,
    )


@router.post("/reviews/{review_id}/reject", response_model=ReviewPublication)
def review_reject(review_id: ReviewId, runtime: RuntimeDep):
    from .reviews import reject_review

    return review_boundary(reject_review, runtime.context.session_factory, review_id)


@router.post("/reviews/{review_id}/publish", response_model=ReviewPublication)
def review_publish(review_id: ReviewId, runtime: RuntimeDep):
    from .reviews import retry_publication

    publish, verify = publication_callbacks(runtime)
    return review_boundary(
        retry_publication,
        runtime.context.session_factory,
        review_id,
        publish=publish,
        verify_published=verify,
    )


def evaluation_manager(request):
    ch09 = getattr(request.app.state, "ch09_runtime", None)
    if not ch09 or not ch09.eval_jobs:
        raise HTTPException(status_code=503, detail="评估工作器尚未启用")
    return ch09.eval_jobs


@router.post("/evaluations", status_code=202)
async def evaluation_submit(body: EvaluationRequest, request: Request):
    try:
        job = await evaluation_manager(request).submit(body)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {**job.model_dump(), "status_url": "/api/ch09/evaluations/" + job.run_id}


@router.get("/evaluations/{run_id}", response_model=EvaluationJob)
async def evaluation_read(run_id: str, request: Request):
    try:
        return await asyncio.to_thread(evaluation_manager(request).get, run_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/eval-runs")
def eval_runs(runtime: RuntimeDep, page: Page = 1, page_size: PageSize = 20):
    from sqlalchemy import func, select

    from mewhelp.db.models import EvalRun

    with runtime.context.session_factory() as db:
        total = db.scalar(select(func.count()).select_from(EvalRun))
        rows = db.scalars(
            select(EvalRun)
            .order_by(EvalRun.created_at.desc(), EvalRun.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return {
            "items": [
                {
                    "id": str(r.id),
                    "triggered_by": r.triggered_by,
                    "dataset_size": r.dataset_size,
                    "metrics": r.metrics,
                    "created_at": r.created_at.isoformat() + "Z",
                }
                for r in rows
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
