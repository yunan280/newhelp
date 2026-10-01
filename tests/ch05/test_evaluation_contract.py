import json

import pytest


@pytest.mark.parametrize("raise_error", [False, True])
async def test_evaluator_fails_on_bad_label_or_provider_error(tmp_path, raise_error):
    try:
        from mewhelp.ch05.evaluation import evaluate_prompts
        from mewhelp.ch05.intent import ClassificationResult
    except ImportError:
        pytest.fail("prompt evaluator missing")
    from mewhelp.ch05.limits import TokenUsage

    data = tmp_path / "labels"
    data.mkdir()
    (data / "intents.jsonl").write_text(
        json.dumps(
            {"id": "x", "question": "退款怎么申请", "expected": "退款退货"}, ensure_ascii=False
        ),
        encoding="utf-8",
    )

    async def classify(text, *, model):
        if raise_error:
            raise RuntimeError("provider unavailable")
        return ClassificationResult(
            intent="闲聊",
            usage=TokenUsage(),
            origin="llm",
            raw='{"intent":"闲聊"}',
            response_model="test",
        )

    assert (
        await evaluate_prompts(data, tmp_path / "results", part="intents", classifier=classify) == 1
    )


def test_decision_evaluator_rejects_wrong_actions_and_missing_clarification():
    from mewhelp.ch05 import evaluation

    assert hasattr(evaluation, "check_decision"), "decision validator missing"
    assert evaluation.check_decision(
        {"reply_mode": "answer", "actions": ["handoff"]}, {"reply_mode": "clarify", "actions": []}
    )
    assert evaluation.check_decision(
        {"reply_mode": "answer", "actions": ["create_ticket"]},
        {"reply_mode": "answer", "actions": ["handoff"]},
    )


def test_reuse_rejects_stale_hash_and_false_success(tmp_path):
    from mewhelp.ch05 import evaluation

    assert hasattr(evaluation, "summarize_evaluations"), "validated summary missing"
    data = tmp_path / "dataset"
    data.mkdir()
    intents = [{"id": "i", "question": "退款怎么申请", "expected": "退款退货"}]
    decisions = [
        {
            "id": "d",
            "question": "查物流",
            "observations": [],
            "expected": {"reply_mode": "clarify", "actions": []},
        }
    ]
    for name, cases in [("intents.jsonl", intents), ("agent-decisions.jsonl", decisions)]:
        (data / name).write_text("\n".join(json.dumps(c) for c in cases), encoding="utf-8")
    dirs = {}
    for part, cases, filename in [
        ("intents", intents, "intents.jsonl"),
        ("decisions", decisions, "agent-decisions.jsonl"),
    ]:
        target = tmp_path / part
        target.mkdir()
        rows = [{**cases[0], "passed": True}]
        if part == "intents":
            rows[0]["actual"] = "闲聊"  # a lying passed flag must not hide the mismatch
        else:
            rows[0]["actual"] = {"reply_mode": "clarify", "actions": [], "tools": []}
        (target / "results.json").write_text(json.dumps(rows), encoding="utf-8")
        (target / "summary.json").write_text(
            json.dumps(
                {
                    "hash": evaluation.evaluation_hash(data / filename),
                    "passed": 1,
                    "total": 1,
                    "service_errors": 0,
                }
            ),
            encoding="utf-8",
        )
        dirs[part] = target
    assert (
        evaluation.summarize_evaluations(
            data,
            tmp_path / "bad-label",
            intents_dir=dirs["intents"],
            decisions_dir=dirs["decisions"],
        )
        == 1
    )
    (dirs["intents"] / "results.json").write_text(
        json.dumps([{**intents[0], "actual": "退款退货", "passed": True}])
    )
    assert (
        evaluation.summarize_evaluations(
            data, tmp_path / "valid", intents_dir=dirs["intents"], decisions_dir=dirs["decisions"]
        )
        == 0
    )
    (dirs["decisions"] / "summary.json").write_text(json.dumps({"hash": "stale"}))
    assert (
        evaluation.summarize_evaluations(
            data, tmp_path / "stale", intents_dir=dirs["intents"], decisions_dir=dirs["decisions"]
        )
        == 1
    )
