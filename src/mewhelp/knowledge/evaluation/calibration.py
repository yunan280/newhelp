"""Tune only on observed calibration scores, with explicit reject-all candidate."""

import math
import re
from dataclasses import dataclass

from .dataset import EvalCase


@dataclass(frozen=True)
class CalibrationResult:
    threshold: float
    model_metadata: dict
    corpus_hash: str
    query_hash: str
    false_allow: int
    false_refuse: int


def calibrate_threshold(
    cases: list[EvalCase],
    top_scores: dict[str, float | None],
    model_metadata: dict,
    *,
    corpus_hash: str,
    query_hash: str,
) -> CalibrationResult:
    if (
        not cases
        or any(case.split != "calibration" for case in cases)
        or len({case.id for case in cases}) != len(cases)
        or set(top_scores) != {case.id for case in cases}
    ):
        raise ValueError("use only complete, unique calibration cases and their observed scores")
    if (
        model_metadata.get("model_id") != "BAAI/bge-reranker-v2-m3"
        or model_metadata.get("score_transform") != "sigmoid"
        or not model_metadata.get("revision")
        or model_metadata.get("max_length") != 8192
        or any(not re.fullmatch(r"[0-9a-f]{64}", item) for item in (corpus_hash, query_hash))
    ):
        raise ValueError("verified model metadata and dataset hashes required")
    observed = []
    for value in top_scores.values():
        if value is not None:
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("invalid observed sigmoid score")
            observed.append(float(value))
    if not observed:
        raise ValueError("no finite observed scores; cannot calibrate a threshold")
    candidates = {*observed, math.nextafter(max(observed), math.inf)}

    def errors(threshold):
        allowed = {
            key for key, score in top_scores.items() if score is not None and score >= threshold
        }
        false_allow = sum(case.should_refuse and case.id in allowed for case in cases)
        false_refuse = sum(not case.should_refuse and case.id not in allowed for case in cases)
        return false_allow, false_refuse, -threshold

    threshold = min(candidates, key=errors)
    false_allow, false_refuse, _ = errors(threshold)
    return CalibrationResult(
        threshold, model_metadata, corpus_hash, query_hash, false_allow, false_refuse
    )
