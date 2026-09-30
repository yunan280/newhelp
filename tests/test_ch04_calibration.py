import math
from dataclasses import replace

import pytest

from mewhelp.knowledge.filters import SearchFilters


def case(name, refuse, split="calibration"):
    from mewhelp.knowledge.evaluation.dataset import EvalCase

    return EvalCase(
        name,
        name,
        "unanswerable" if refuse else "model_exact",
        "easy",
        split,
        SearchFilters(),
        set() if refuse else {1},
        "标注",
        [] if refuse else ["事实"],
        refuse,
        "原文核查",
    )


def metadata():
    return {
        "model_id": "BAAI/bge-reranker-v2-m3",
        "revision": "verified-revision",
        "max_length": 8192,
        "score_transform": "sigmoid",
    }


def calibrate(cases, scores):
    from mewhelp.knowledge.evaluation.calibration import calibrate_threshold

    return calibrate_threshold(cases, scores, metadata(), corpus_hash="c" * 64, query_hash="a" * 64)


def test_minimize_false_allow_then_maximize_known_coverage():
    result = calibrate(
        [case("k1", False), case("k2", False), case("u", True)], {"k1": 0.8, "k2": 0.4, "u": 0.5}
    )
    assert result.threshold == 0.8
    assert result.false_allow == 0 and result.false_refuse == 1


def test_all_reject_threshold_is_finite_and_reported():
    result = calibrate([case("k", False), case("u", True)], {"k": 0.3, "u": 0.9})
    assert result.threshold == math.nextafter(0.9, math.inf)
    assert result.false_allow == 0 and result.false_refuse == 1


def test_empty_evidence_is_never_allowed():
    result = calibrate([case("k", False), case("u", True)], {"k": 0.8, "u": None})
    assert result.false_allow == 0 and result.false_refuse == 0


def test_test_split_is_rejected_for_threshold_tuning():
    with pytest.raises(ValueError):
        calibrate([case("test", False, "test")], {"test": 0.7})


@pytest.mark.parametrize("scores", [{"k": float("nan")}, {"k": True}, {}, {"k": 2.0}, {"k": None}])
def test_invalid_or_unobserved_scores_cannot_make_a_threshold(scores):
    with pytest.raises(ValueError):
        calibrate([case("k", False)], scores)


def test_model_metadata_matches_online_reader(tmp_path):
    import json
    from dataclasses import asdict

    from mewhelp.knowledge.answering import load_relevance_threshold

    result = calibrate([case("k", False)], {"k": 0.8})
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps(asdict(result)), encoding="utf-8")
    assert load_relevance_threshold(path, metadata()) == 0.8
    with pytest.raises(ValueError):
        load_relevance_threshold(path, {**metadata(), "score_transform": "raw"})
    with pytest.raises(ValueError):
        load_relevance_threshold(path, {**metadata(), "revision": "different"})


def test_noncalibration_case_is_not_hidden_by_replacing_scores():
    with pytest.raises(ValueError):
        calibrate([replace(case("k", False), split="test")], {"k": 0.8})
