import json
from importlib import import_module

import pytest


def evaluator_module():
    try:
        return import_module("mewhelp.ch06.evaluation")
    except ImportError:
        pytest.fail("missing auditable evaluation runner")


def dataset_at(path):
    path.mkdir()
    (path / "intents.jsonl").write_text(
        json.dumps(
            {
                "id": "i-1",
                "split": "acceptance",
                "question": "查物流",
                "expected": {"intent": "物流"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def valid_result(intent="物流"):
    return {
        "actual": {"intent": intent, "confidence": 0.9},
        "raw_responses": [
            {"content": json.dumps({"intent": intent, "confidence": 0.9}), "repair": False},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
        "calls": {"classifier": 1},
    }


async def run(tmp_path, function):
    module = evaluator_module()
    dataset = dataset_at(tmp_path / "data")
    module.freeze_dataset(dataset)
    outdir = tmp_path / "out"
    code = await module.evaluate(
        dataset,
        outdir,
        parts=("intents",),
        mode="primary",
        calibration_path=None,
        evaluators={"intents": function},
    )
    return code, json.loads((outdir / "summary.json").read_text())


async def test_provider_error_is_recorded_and_nonzero(tmp_path):
    async def broken(case, **kwargs):
        raise RuntimeError("upstream unavailable")

    code, summary = await run(tmp_path, broken)
    assert code == 1 and summary["service_errors"] == 1 and summary["passed"] == 0


async def test_fake_passed_flag_cannot_hide_wrong_label(tmp_path):
    async def wrong(case, **kwargs):
        return {**valid_result("退款退货"), "passed": True}

    code, summary = await run(tmp_path, wrong)
    assert code == 1 and summary["failed"] == 1


async def test_missing_output_is_not_a_skipped_success(tmp_path):
    async def missing(case, **kwargs):
        return None

    code, summary = await run(tmp_path, missing)
    assert code == 1 and summary["failed"] == 1 and summary["total"] == 1


async def test_changed_frozen_labels_fail_without_running_model(tmp_path):
    module = evaluator_module()
    dataset = dataset_at(tmp_path / "data")
    module.freeze_dataset(dataset)
    (dataset / "intents.jsonl").write_text("{}\n", encoding="utf-8")
    requests = []

    async def unused(case, **kwargs):
        requests.append(case)
        return valid_result()

    code = await module.evaluate(
        dataset,
        tmp_path / "out",
        parts=("intents",),
        mode="primary",
        calibration_path=None,
        evaluators={"intents": unused},
    )
    assert code == 1 and requests == []
    summary = json.loads((tmp_path / "out" / "summary.json").read_text())
    assert any("frozen" in error for error in summary["integrity_errors"])


async def test_removed_frozen_case_is_detected(tmp_path):
    module = evaluator_module()
    dataset = dataset_at(tmp_path / "data")
    module.freeze_dataset(dataset)
    (dataset / "intents.jsonl").unlink()
    code = await module.evaluate(
        dataset,
        tmp_path / "out",
        parts=("intents",),
        mode="primary",
        calibration_path=None,
        evaluators={},
    )
    assert code == 1


async def test_original_and_repaired_json_rates_are_separate(tmp_path):
    async def repaired(case, **kwargs):
        result = valid_result()
        result["raw_responses"] = [
            {"content": "bad", "repair": False},
            {**result["raw_responses"][0], "repair": True},
        ]
        return result

    code, summary = await run(tmp_path, repaired)
    assert code == 0 and summary["passed"] == 1
    assert summary["original_json_parsed"] == 0
    assert summary["final_json_parsed"] == 1


async def test_invalid_confidence_fails_even_when_intent_matches(tmp_path):
    async def malformed(case, **kwargs):
        result = valid_result()
        result["actual"]["confidence"] = "high"
        return result

    code, summary = await run(tmp_path, malformed)
    assert code == 1 and summary["failed"] == 1


async def test_existing_report_cannot_be_overwritten(tmp_path):
    module = evaluator_module()
    dataset = dataset_at(tmp_path / "data")
    module.freeze_dataset(dataset)
    outdir = tmp_path / "out"
    outdir.mkdir()
    with pytest.raises(FileExistsError):
        await module.evaluate(
            dataset,
            outdir,
            parts=("intents",),
            mode="primary",
            calibration_path=None,
            evaluators={},
        )


def test_refreeze_refuses_modified_labels(tmp_path):
    module = evaluator_module()
    dataset = dataset_at(tmp_path / "data")
    original = module.freeze_dataset(dataset)
    assert module.freeze_dataset(dataset) == original
    (dataset / "intents.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="frozen"):
        module.freeze_dataset(dataset)
