from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from mewhelp.ch05.limits import AgentLimits
from mewhelp.ch05.state import WorkflowContext
from mewhelp.ch09.confidence import ConfidenceProfile
from mewhelp.knowledge.evaluation.dataset import EvalCase
from mewhelp.knowledge.filters import SearchFilters
from mewhelp.knowledge.retrieval import RankedChunk, RetrievalResult
from mewhelp.knowledge.store import KnowledgeChunk, snapshot_chunk


@pytest.fixture(autouse=True)
def no_network_model_in_unit_tests(monkeypatch):
    import mewhelp.llm

    def blocked(*args, **kwargs):
        raise RuntimeError("unit test must inject its model")

    monkeypatch.setattr(mewhelp.llm, "get_chat_model", blocked)


def profile():
    return ConfidenceProfile(
        5,
        0.5,
        (0.75, 0.25, 0),
        0.8,
        {"test_model": True},
        {"corpus_hash": "a" * 64, "query_hash": "b" * 64},
        "c" * 64,
    )


def case():
    return EvalCase(
        "test",
        "原始问题",
        "model_exact",
        "easy",
        "test",
        SearchFilters(),
        {10},
        "答案",
        ["答案"],
        False,
        "测试控制",
    )


def raw():
    chunks = [
        snapshot_chunk(
            KnowledgeChunk(
                id=i,
                category="测试",
                questions="问题",
                answer="答案",
                content_type="faq",
                section_path="测试",
                is_key_clause=False,
            )
        )
        for i in range(1, 11)
    ]
    return RetrievalResult(chunks, [RankedChunk(c, 0.99 - i * 0.01) for i, c in enumerate(chunks)])


@pytest.mark.parametrize('parsed,expected', [
    (None, 'invalid_generation'),
    ({'answerable': True, 'reason': '有依据', 'answer': '答案[99]。', 'citation_numbers': [99]}, 'invalid_citation'),
    ({'answerable': False, 'reason': '知识不足', 'answer': '', 'citation_numbers': []}, None),
])
async def test_real_generation_failure_is_not_counted_as_normal_refusal(monkeypatch, parsed, expected):
    from langchain_core.messages import AIMessage

    from mewhelp.ch09 import evaluation
    from mewhelp.knowledge.answering import AnswerAssessment

    class Model:
        def with_structured_output(self, schema, **kwargs):
            return self

        async def ainvoke(self, messages):
            return {'raw': AIMessage('provider result', usage_metadata={'input_tokens': 10, 'output_tokens': 2, 'total_tokens': 12}),
                    'parsed': AnswerAssessment.model_validate(parsed) if parsed else None,
                    'parsing_error': ValueError('malformed provider JSON') if parsed is None else None}

    monkeypatch.setattr(evaluation, 'retrieve_current_evidence', lambda *a, **kw: raw())
    workflow = WorkflowContext(None, lambda *a, **kw: Model(), None, AgentLimits())
    row = await evaluation.evaluate_case(case(), retrieval=None, workflow=workflow, profile=profile())
    assert row['refused'] is True
    if expected:
        assert expected in row['error'] and row['error_stage'] == 'generation'
        assert evaluation.summarize_rows([row])['errors'] == 1
    else:
        assert row['error'] is None and evaluation.summarize_rows([row])['errors'] == 0


async def test_eval_uses_raw_question_gate_and_generation_top5(monkeypatch):
    from mewhelp.ch09 import evaluation
    from mewhelp.knowledge.evaluation.judge import JudgeResult

    seen = {}

    def retrieve(q, *, rag, filters):
        seen["question"] = q
        return raw()

    async def generate(state, context, emit, *, record_pool):
        assert not record_pool and state["question"] == "原始问题"
        assert (
            len(state["evidence"]["sources"])
            == len(state["evidence"]["retrieved_chunks"]["chunks"])
            == 5
        )
        return {
            "answer": "答案[1]",
            "refused": False,
            "knowledge_raw_usage": {"input_tokens": 5, "output_tokens": 2},
        }

    async def judge(*args, **kwargs):
        return JudgeResult([], None, None)

    monkeypatch.setattr(evaluation, "retrieve_current_evidence", retrieve)
    context = WorkflowContext(None, None, None, AgentLimits())
    result = await evaluation.evaluate_case(
        case(),
        retrieval=SimpleNamespace(),
        workflow=context,
        profile=profile(),
        generator=generate,
        judge=judge,
    )
    assert seen["question"] == "原始问题" and len(result["final"]) == 10
    assert (
        result["final_recall5"] == 0
        and result["final_recall10"] == 1
        and result["final_mrr10"] == 0.1
    )
    assert result["faithfulness"] is None and not result["error"]


async def test_refusal_na_and_judge_error_remain_distinct(monkeypatch):
    from mewhelp.ch09 import evaluation
    from mewhelp.knowledge.evaluation.judge import JudgeResult

    context = WorkflowContext(None, None, None, AgentLimits())
    monkeypatch.setattr(
        evaluation, "retrieve_current_evidence", lambda *args, **kwargs: RetrievalResult([], [])
    )

    async def never(*args, **kwargs):
        pytest.fail("gate must bypass model")

    refused = await evaluation.evaluate_case(
        case(), retrieval=None, workflow=context, profile=profile(), generator=never
    )
    assert refused["refused"] and refused["faithfulness"] is None and refused["final_recall5"] == 0
    no_gt = replace(case(), relevant_chunk_ids=set(), should_refuse=True)
    row = await evaluation.evaluate_case(
        no_gt, retrieval=None, workflow=context, profile=profile(), generator=never
    )
    assert row["final_recall5"] is None
    monkeypatch.setattr(evaluation, "retrieve_current_evidence", lambda *args, **kwargs: raw())

    async def gen(*args, **kwargs):
        return {"answer": "答案", "refused": False, "knowledge_raw_usage": None}

    async def bad(*args, **kwargs):
        return JudgeResult([], None, "real judge failed")

    failed = await evaluation.evaluate_case(
        case(), retrieval=None, workflow=context, profile=profile(), generator=gen, judge=bad
    )
    summary = evaluation.summarize_rows([refused, row, failed])
    assert summary["faithfulness"] is None and summary["faithfulness_N"] == 0
    assert summary["judge_errors"] == 1 and summary["final_recall5_N"] == 2


def test_production_collection_and_path_escape_rejected(tmp_path):
    from mewhelp.ch09.evaluation import validate_run

    for rid in ["../escape", "x/y", "X", "bad-id", "x" * 65]:
        with pytest.raises(ValueError):
            validate_run(rid, tmp_path, online_collection="knowledge")
    with pytest.raises(ValueError):
        validate_run("x", tmp_path, online_collection="ch04_eval_x")


def test_resume_rejects_model_or_prompt_drift(tmp_path):
    from mewhelp.ch09.evaluation import atomic_json, resume_rows

    manifest = {"identity_hash": "a" * 64, "run_id": "x"}
    atomic_json(tmp_path / "manifest.json", manifest)
    (tmp_path / "cases.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="漂移"):
        resume_rows(tmp_path, {"identity_hash": "b" * 64, "run_id": "x"}, {"test"})


def test_atomic_rows_preserve_na_and_reject_unowned_case(tmp_path):
    from mewhelp.ch09.evaluation import atomic_json, atomic_rows, resume_rows

    manifest = {"identity_hash": "a" * 64, "run_id": "x"}
    atomic_json(tmp_path / "manifest.json", manifest)
    atomic_rows(tmp_path / "cases.jsonl", [{"id": "test", "terminal": True, "faithfulness": None}])
    assert resume_rows(tmp_path, manifest, {"test"})[0]["faithfulness"] is None
    with pytest.raises(ValueError):
        resume_rows(tmp_path, manifest, {"different"})


async def test_judge_actual_usage_is_saved_without_estimation(monkeypatch):
    from langchain_core.messages import AIMessage

    from mewhelp.ch09 import evaluation
    from mewhelp.knowledge.evaluation.judge import ClaimBatch

    class Model:
        def with_structured_output(self, schema, **kwargs):
            return self

        async def ainvoke(self, messages):
            return {
                "raw": AIMessage(
                    "", usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
                ),
                "parsed": ClaimBatch(
                    claims=[
                        {
                            "text": "答案",
                            "supported": True,
                            "evidence_numbers": [1],
                            "rationale": "原文直接支持",
                        }
                    ]
                ),
                "parsing_error": None,
            }

    context = WorkflowContext(None, lambda *args, **kwargs: Model(), None, AgentLimits())
    monkeypatch.setattr(evaluation, "retrieve_current_evidence", lambda *args, **kwargs: raw())

    async def gen(*args, **kwargs):
        return {"answer": "答案[1]", "refused": False, "knowledge_raw_usage": None}

    row = await evaluation.evaluate_case(
        case(),
        retrieval=None,
        workflow=context,
        profile=profile(),
        generator=gen,
        judge=evaluation.make_judge(context),
    )
    assert row["faithfulness"] == 1 and row["judge_usage"]["total_tokens"] == 10


def test_corrupted_resume_record_is_rejected(tmp_path):
    import json

    from mewhelp.ch09.evaluation import atomic_json, atomic_rows, resume_rows

    manifest = {"identity_hash": "a" * 64, "run_id": "x"}
    atomic_json(tmp_path / "manifest.json", manifest)
    path = tmp_path / "cases.jsonl"
    atomic_rows(path, [{"id": "test", "terminal": True, "faithfulness": None}])
    row = json.loads(path.read_text(encoding="utf-8"))
    row["faithfulness"] = 1
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="完整性"):
        resume_rows(tmp_path, manifest, {"test"})


async def test_interrupted_run_resumes_exact_completed_records_and_new_run_reexecutes(
    ch09_db, tmp_path, monkeypatch
):
    import asyncio
    from collections import Counter

    from mewhelp.ch09 import evaluation
    from mewhelp.knowledge.evaluation.dataset import load_dataset
    from mewhelp.knowledge.retrieval import RetrievalRuntime

    dataset = Path("eval/ch04")
    _, cases = load_dataset(dataset / "corpus.jsonl", dataset / "queries.jsonl")
    cases = [c for c in cases if c.split == "test"]
    index = SimpleNamespace(client=SimpleNamespace(close=lambda: None))

    async def prepare(*args, **kwargs):
        return RetrievalRuntime(ch09_db, None, index, None), cases

    def identity(dataset, profile, workflow, run_id, collection, triggered_by):
        return {
            "run_id": run_id,
            "identity_hash": run_id,
            "dataset_hashes": {},
            "model_metadata": {},
            "prompt_hashes": {},
            "confidence_hash": "test",
            "collection": collection,
        }

    monkeypatch.setattr(evaluation, "prepare_corpus", prepare)
    monkeypatch.setattr(evaluation, "manifest_identity", identity)
    seen = []
    waiting = asyncio.Event()
    pause = True

    async def one(case, **kwargs):
        seen.append(case.id)
        if pause and len(seen) == 4:
            waiting.set()
            await asyncio.Event().wait()
        return {
            "id": case.id,
            "terminal": True,
            "refused": True,
            "should_refuse": case.should_refuse,
            "error": None,
            "judge_error": None,
            "faithfulness": None,
        }

    monkeypatch.setattr(evaluation, "evaluate_case", one)
    task = asyncio.create_task(
        evaluation.evaluate_current_path(
            dataset=dataset, workdir=tmp_path / "x", run_id="x", profile=profile()
        )
    )
    await asyncio.wait_for(waiting.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    pause = False
    result = await evaluation.evaluate_current_path(
        dataset=dataset, workdir=tmp_path / "x", run_id="x", profile=profile(), resume=True
    )
    assert (
        result["complete"] and result["processed"] == 40 and result["status"] == "completed_all_na"
    )
    counts = Counter(seen)
    assert all(counts[c.id] == 1 for c in cases[:3]) and counts[cases[3].id] == 2
    fresh = await evaluation.evaluate_current_path(
        dataset=dataset, workdir=tmp_path / "y", run_id="y", profile=profile()
    )
    assert fresh["complete"] and len(seen) == 81
