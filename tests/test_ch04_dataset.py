import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "eval" / "ch04"


def read_records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_frozen_dataset_has_balanced_splits_and_original_ground_truth():
    from mewhelp.knowledge.evaluation.dataset import load_dataset

    corpus, cases = load_dataset(ROOT / "corpus.jsonl", ROOT / "queries.jsonl")
    assert len(corpus) >= 80
    assert len(cases) == 60
    assert sum(case.split == "calibration" for case in cases) == 20
    assert sum(case.split == "test" for case in cases) == 40
    assert all(case.label_basis and case.reference_answer for case in cases)


@pytest.mark.parametrize("damage", [
    "missing_id", "duplicate_case", "duplicate_source", "type", "difficulty", "split",
    "refusal_gt", "count", "split_balance", "numeric_id", "wrong_id", "key_fact",
    "filter_excludes_gt", "duplicate_relevant_id",
])
def test_invalid_dataset_is_rejected(tmp_path, damage):
    from mewhelp.knowledge.evaluation.dataset import load_dataset

    corpus = read_records(ROOT / "corpus.jsonl")
    cases = read_records(ROOT / "queries.jsonl")
    answerable = next(case for case in cases if not case["should_refuse"])
    unknown = next(case for case in cases if case["should_refuse"])
    if damage == "missing_id":
        answerable["relevant_chunk_ids"] = ["1"]
    elif damage == "duplicate_case":
        cases[1]["id"] = cases[0]["id"]
    elif damage == "duplicate_source":
        corpus[1] = corpus[0]
    elif damage in ("type", "difficulty", "split"):
        cases[0][{"type": "query_type"}.get(damage, damage)] = "unknown"
    elif damage == "refusal_gt":
        unknown["relevant_chunk_ids"] = answerable["relevant_chunk_ids"]
    elif damage == "count":
        cases.pop()
    elif damage == "split_balance":
        cases[0]["split"] = "test" if cases[0]["split"] == "calibration" else "calibration"
    elif damage == "numeric_id":
        answerable["relevant_chunk_ids"] = [int(answerable["relevant_chunk_ids"][0])]
    elif damage == "wrong_id":
        corpus[0]["chunk_id"] = "1"
    elif damage == "key_fact":
        answerable["key_facts"] = ["原文并没有这项保证"]
    elif damage == "filter_excludes_gt":
        answerable["filters"] = {"product_category": "不在语料里的品类"}
    elif damage == "duplicate_relevant_id":
        answerable["relevant_chunk_ids"] *= 2
    cp, qp = tmp_path / "corpus.jsonl", tmp_path / "queries.jsonl"
    cp.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in corpus), encoding="utf-8")
    qp.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in cases), encoding="utf-8")
    with pytest.raises(ValueError):
        load_dataset(cp, qp)
