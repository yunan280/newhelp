import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from mewhelp.ch09.evaluation_jobs import EvaluationRequest, persist_eval_run
from mewhelp.db.models import EvalRun

pytestmark = pytest.mark.mysql


def summary(run):
    return {
        "dataset_size": 40,
        "processed": 40,
        "complete": True,
        "status": "completed_all_na",
        "metrics": {
            "faithfulness": None,
            "faithfulness_N": 0,
            "N": 40,
            "说明": "控制测试，不是模型评估",
            "_meta": {"run_id": run, "identity_hash": "a" * 64, "result_hash": "b" * 64},
        },
    }


async def test_mysql_independent_connections_register_same_run_once(ch09_mysql):
    factory = sessionmaker(ch09_mysql, expire_on_commit=False)
    request = EvaluationRequest(run_id="control_x")

    def persist():
        return persist_eval_run(
            factory, request=request, summary=summary(request.run_id), engine=ch09_mysql
        )

    ids = await asyncio.gather(asyncio.to_thread(persist), asyncio.to_thread(persist))
    assert ids[0] == ids[1]
    with factory() as db:
        rows = db.scalars(select(EvalRun)).all()
        assert len(rows) == 1 and rows[0].metrics["faithfulness"] is None
        assert rows[0].metrics["说明"] == "控制测试，不是模型评估"
    other = EvaluationRequest(run_id="control_y", triggered_by="定时")
    assert (
        persist_eval_run(factory, request=other, summary=summary(other.run_id), engine=ch09_mysql)
        != ids[0]
    )


def test_mysql_partial_and_changed_completion_are_not_inserted(ch09_mysql):
    factory = sessionmaker(ch09_mysql, expire_on_commit=False)
    request = EvaluationRequest(run_id="control_x")
    partial = summary(request.run_id)
    partial["processed"] = 39
    partial["complete"] = False
    with pytest.raises(ValueError):
        persist_eval_run(factory, request=request, summary=partial, engine=ch09_mysql)
    persist_eval_run(factory, request=request, summary=summary(request.run_id), engine=ch09_mysql)
    changed = summary(request.run_id)
    changed["metrics"]["_meta"]["result_hash"] = "c" * 64
    with pytest.raises(ValueError):
        persist_eval_run(factory, request=request, summary=changed, engine=ch09_mysql)
    with factory() as db:
        assert len(db.scalars(select(EvalRun)).all()) == 1


def test_mysql_measured_fraction_replay_preserves_run_identity(ch09_mysql):
    factory = sessionmaker(ch09_mysql, expire_on_commit=False)
    request = EvaluationRequest(run_id='fraction_control')
    measured = summary(request.run_id)
    measured['metrics']['faithfulness'] = 0.9987983703613281
    measured['metrics']['faithfulness_N'] = 35
    first = persist_eval_run(factory, request=request, summary=measured, engine=ch09_mysql)
    assert persist_eval_run(factory, request=request, summary=measured, engine=ch09_mysql) == first
    measured['metrics']['faithfulness'] += .00001
    with pytest.raises(ValueError, match='不同评估结果'):
        persist_eval_run(factory, request=request, summary=measured, engine=ch09_mysql)
