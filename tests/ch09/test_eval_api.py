from types import SimpleNamespace

import httpx
from fastapi import FastAPI


async def test_eval_submit_status_and_unmodified_metric_names(ch09_db):
    from mewhelp.ch09.api import router
    from mewhelp.ch09.evaluation_jobs import EvaluationJob
    from mewhelp.db.models import EvalRun

    with ch09_db.begin() as db:
        db.add(
            EvalRun(
                triggered_by="手动",
                dataset_size=40,
                metrics={
                    "faithfulness": None,
                    "faithfulness_N": 0,
                    "final_recall5": 0.8,
                    "final_recall5_N": 32,
                    "_meta": {"run_id": "unit"},
                },
            )
        )

    class Manager:
        async def submit(self, request):
            return self.get(request.run_id)

        def get(self, run_id):
            if run_id != "unit":
                raise LookupError("评估任务不存在")
            return EvaluationJob(
                run_id=run_id, status="running", processed=1, artifact_dir="unit-test"
            )

    app = FastAPI()
    app.include_router(router)
    app.state.ch09_runtime = SimpleNamespace(eval_jobs=Manager())
    app.state.ch05_runtime = SimpleNamespace(context=SimpleNamespace(session_factory=ch09_db))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        submitted = await client.post("/api/ch09/evaluations", json={"run_id": "unit"})
        status = await client.get(submitted.json()["status_url"])
        missing = await client.get("/api/ch09/evaluations/missing")
        bad = await client.post("/api/ch09/evaluations", json={"run_id": "../escape"})
        runs = await client.get("/api/ch09/eval-runs")
    assert submitted.status_code == 202 and status.json()["processed"] == 1
    assert missing.status_code == 404 and bad.status_code == 422
    metrics = runs.json()["items"][0]["metrics"]
    assert metrics["faithfulness"] is None and metrics["final_recall5_N"] == 32
