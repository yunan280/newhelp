import asyncio
from contextlib import contextmanager

import pytest
from sqlalchemy import select

from mewhelp.db.models import EvalRun


@pytest.fixture
def lock(monkeypatch):
    from mewhelp.ch09 import evaluation_jobs

    @contextmanager
    def fake(*args, **kwargs):
        yield True

    monkeypatch.setattr(evaluation_jobs, "named_lock", fake)


def summary(run="x", processed=40):
    return {
        "dataset_size": 40,
        "processed": processed,
        "complete": processed == 40,
        "status": "completed_all_na",
        "metrics": {
            "faithfulness": None,
            "faithfulness_N": 0,
            "N": processed,
            "说明": "拒答不冒充满分",
            "_meta": {"run_id": run, "identity_hash": "a" * 64, "result_hash": "b" * 64},
        },
    }


def test_partial_run_has_no_eval_row(ch09_db, lock):
    from mewhelp.ch09.evaluation_jobs import EvaluationRequest, persist_eval_run

    with pytest.raises(ValueError, match="40"):
        persist_eval_run(
            ch09_db,
            request=EvaluationRequest(run_id="x"),
            summary=summary(processed=39),
            engine=ch09_db.kw["bind"],
        )
    with ch09_db() as db:
        assert db.scalar(select(EvalRun)) is None


def test_repeated_completion_returns_same_row_and_changed_run_conflicts(ch09_db, lock):
    from mewhelp.ch09.evaluation_jobs import EvaluationRequest, persist_eval_run

    req = EvaluationRequest(run_id="x")
    one = persist_eval_run(ch09_db, request=req, summary=summary(), engine=ch09_db.kw["bind"])
    assert one == persist_eval_run(
        ch09_db, request=req, summary=summary(), engine=ch09_db.kw["bind"]
    )
    changed = summary()
    changed["metrics"]["_meta"]["result_hash"] = "c" * 64
    with pytest.raises(ValueError):
        persist_eval_run(ch09_db, request=req, summary=changed, engine=ch09_db.kw["bind"])


async def test_manager_runs_single_task_and_cancellation_is_interrupted(ch09_db, lock, tmp_path):
    from mewhelp.ch09.evaluation_jobs import EvalJobManager, EvaluationRequest

    started = asyncio.Event()

    async def runner(**kwargs):
        kwargs["on_progress"](1)
        started.set()
        await asyncio.Event().wait()

    manager = EvalJobManager(
        ch09_db, dataset=tmp_path, artifact_dir=tmp_path, profile=None, runner=runner
    )
    request = EvaluationRequest(run_id="x")
    job = await manager.submit(request)
    await asyncio.wait_for(started.wait(), 2)
    assert (await manager.submit(request)).run_id == job.run_id
    with pytest.raises(RuntimeError, match="忙"):
        await manager.submit(EvaluationRequest(run_id="y"))
    await manager.aclose()
    assert manager.get("x").status == "interrupted" and manager.get("x").processed == 1
    with ch09_db() as db:
        assert db.scalar(select(EvalRun)) is None


@pytest.mark.parametrize("run", ["../x", "bad/id", "Bad", "x-y"])
def test_request_rejects_path_traversal(run):
    from mewhelp.ch09.evaluation_jobs import EvaluationRequest

    with pytest.raises(ValueError):
        EvaluationRequest(run_id=run)


async def test_receipt_write_failure_releases_global_lock(ch09_db, tmp_path, monkeypatch):
    from mewhelp.ch09 import evaluation_jobs

    released = []

    @contextmanager
    def locked(*args, **kwargs):
        try:
            yield True
        finally:
            released.append(True)

    def fail(*args):
        raise OSError("disk write failed")

    monkeypatch.setattr(evaluation_jobs, "named_lock", locked)
    monkeypatch.setattr(evaluation_jobs, "atomic_json", fail)
    manager = evaluation_jobs.EvalJobManager(
        ch09_db, dataset=tmp_path, artifact_dir=tmp_path, profile=None
    )
    with pytest.raises(OSError):
        await manager.submit(evaluation_jobs.EvaluationRequest(run_id="x"))
    assert released == [True] and manager.task is None


async def test_setup_failure_is_durable_and_explicit_resume_can_register(ch09_db, tmp_path, lock):
    from mewhelp.ch09.evaluation_jobs import EvalJobManager, EvaluationRequest

    fixed = False

    async def runner(**kwargs):
        if not fixed:
            raise RuntimeError("setup failed")
        kwargs["on_progress"](40)
        return summary(kwargs["run_id"])

    manager = EvalJobManager(
        ch09_db, dataset=tmp_path, artifact_dir=tmp_path, profile=None, runner=runner
    )
    await manager.submit(EvaluationRequest(run_id="x"))
    await manager.task
    replacement = EvalJobManager(
        ch09_db, dataset=tmp_path, artifact_dir=tmp_path, profile=None, runner=runner
    )
    assert "setup failed" in replacement.get("x").error
    with pytest.raises(ValueError):
        await replacement.submit(EvaluationRequest(run_id="x"))
    fixed = True
    await replacement.submit(EvaluationRequest(run_id="x", resume=True))
    await replacement.task
    assert replacement.get("x").status == "completed_all_na" and replacement.get("x").eval_run_id
