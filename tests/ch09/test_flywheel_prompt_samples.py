from pathlib import Path

import pytest


def test_frozen_flywheel_labels_cover_conditions_and_not_answer_similarity():
    from mewhelp.ch09.prompt_eval import load_flywheel_samples

    samples = load_flywheel_samples(Path("eval/ch09"))
    assert len(samples["normalization"]) >= 12 and len(samples["dedup"]) >= 16
    assert all(r["label_basis"] for rows in samples.values() for r in rows)
    assert all(r["snapshot"] is None for r in samples["normalization"])
    assert any(not r["expected_ids"] for r in samples["dedup"])
    assert all(len(r["candidates"]) <= 8 for r in samples["dedup"])


def test_bad_flywheel_labels_fail_before_model(tmp_path):
    from mewhelp.ch09.prompt_eval import load_flywheel_samples

    (tmp_path / "normalization-samples.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "dedup-samples.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_flywheel_samples(tmp_path)
