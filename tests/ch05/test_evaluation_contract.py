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
