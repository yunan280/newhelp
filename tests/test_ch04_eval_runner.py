from pathlib import Path

import pytest

from mewhelp.knowledge.answering import AnswerResult, RagRuntime
from mewhelp.knowledge.evaluation.dataset import load_dataset
from mewhelp.knowledge.query import QueryUnderstanding
from mewhelp.knowledge.retrieval import RetrievalResult

ROOT = Path(__file__).resolve().parents[1] / "eval" / "ch04"


class Index:
    def __init__(self):
        self.rows = {}

    def ensure_collection(self):
        pass

    def upsert(self, snapshot, vector):
        self.rows[snapshot.id] = snapshot

    def audit(self, snapshots):
        return []


@pytest.mark.asyncio
async def test_ablations_share_query_but_do_not_share_rerank_gate(tmp_path, monkeypatch):
    from mewhelp.knowledge.evaluation import runner

    corpus, cases = load_dataset(ROOT / "corpus.jsonl", ROOT / "queries.jsonl")
    index, normalize_calls, calls = Index(), [], []
    monkeypatch.setattr(runner, "_new_index", lambda collection: index)
    monkeypatch.setattr(runner, "embed_texts", lambda texts: [[0.0] * 1024 for _ in texts])
    monkeypatch.setattr(runner, "_model_metadata", lambda: {"reranker": {}, "embedding": {}})

    async def normalize(question):
        normalize_calls.append(question)
        return QueryUnderstanding(question, question, question, "knowledge", [])

    monkeypatch.setattr(runner, "understand_query", normalize)
    monkeypatch.setattr(
        runner, "_runtime", lambda factory, index: RagRuntime(None, None, factory, 0.0, 32000)
    )

    async def answer(runtime, query, **kwargs):
        calls.append((query, kwargs))
        return AnswerResult("依据不足", [], True, None, RetrievalResult([], []))

    monkeypatch.setattr(runner, "answer_question", answer)
    monkeypatch.setattr(runner, "retrieve_evidence", lambda *a, **kw: RetrievalResult([], []))
    await runner.prepare_run(
        corpus, cases, workdir=tmp_path, collection="ch04_eval_unit", run_id="unit"
    )
    path = await runner.run_comparison(
        corpus, cases, workdir=tmp_path, collection="ch04_eval_unit", run_id="unit"
    )
    assert path.is_file()
    assert len(normalize_calls) == len(cases)
    assert [kwargs["strategy"] for _, kwargs in calls[:4]] == [
        "dense",
        "bm25",
        "hybrid",
        "hybrid_rerank",
    ]
    assert len(calls) == 40 * 4
    assert all(
        not kwargs["apply_relevance_gate"] and not kwargs["record_pool"] for _, kwargs in calls
    )
    assert all(calls[i][0] is calls[i + 1][0] for i in range(0, len(calls), 4))
    import json

    report = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    overall = report["strategies"]["dense"]["overall"]
    assert overall["N"] == 40 and overall["faithfulness"] is None
    assert overall["scored_coverage"] == 0.0
    assert report["strategies"]["dense"]["query_type"]["model_exact"]["N"] == 8
    assert report["strategies"]["dense"]["difficulty"]["easy"]["N"] == 15
    assert report["strategies"]["dense"]["cross"]["unanswerable/hard"]["N"] == 2

    # Re-running prepare reuses the query cache and only touches the isolated collection.
    await runner.prepare_run(
        corpus, cases, workdir=tmp_path, collection="ch04_eval_unit", run_id="unit"
    )
    assert len(normalize_calls) == len(cases)
    monkeypatch.setattr(runner, "QUERY_SYSTEM", "changed normalization prompt")
    with pytest.raises(ValueError, match="prompt"):
        await runner.run_comparison(
            corpus, cases, workdir=tmp_path, collection="ch04_eval_unit", run_id="unit"
        )


@pytest.mark.asyncio
async def test_unprepared_or_production_collection_cannot_be_compared(tmp_path):
    from mewhelp.knowledge.evaluation.runner import run_comparison

    for collection in ("knowledge", "ch04_eval_unit"):
        with pytest.raises(ValueError):
            await run_comparison([], [], workdir=tmp_path, collection=collection, run_id="unit")


def test_errors_are_counted_separately_from_refusals_and_recall_misses():
    from mewhelp.knowledge.evaluation.runner import summarize

    rows = [
        {
            "should_refuse": False,
            "refused": True,
            "error": None,
            "judge_error": None,
            "faithfulness": None,
            "candidate_recall50": 0.0,
            "candidate_mrr50": 0.0,
            "final_recall5": 0.0,
            "final_recall10": 0.0,
            "final_mrr10": 0.0,
        },
        {
            "should_refuse": True,
            "refused": None,
            "error": "index unavailable",
            "judge_error": None,
            "faithfulness": None,
        },
    ]
    summary = summarize(rows)
    assert summary["N"] == 2 and summary["errors"] == 1
    assert summary["correct_refusals"] == 0 and summary["false_refusals"] == 1
    assert summary["candidate_recall50"] == 0.0 and summary["faithfulness"] is None
    assert summary["answer_coverage"] == 0.0 and summary["scored_coverage"] == 0.0
