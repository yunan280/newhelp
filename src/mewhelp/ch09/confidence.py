"""Calibrated evidence signals, independent of the mutable online FAQ corpus."""

import hashlib
import inspect
import itertools
import json
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ConfidenceProfile:
    top_k: int
    effective_cutoff: float
    weights: tuple[float, float, float]
    threshold: float
    model_metadata: dict
    dataset_hashes: dict
    retrieval_config_hash: str

    def __post_init__(self):
        object.__setattr__(self, 'weights', tuple(self.weights))
        if (len(self.weights) != 3 or any(type(w) not in (float, int) or
            not math.isfinite(w) or w < 0 or w * 4 != round(w * 4) for w in self.weights)
            or sum(self.weights) != 1 or self.weights[0] < .5):
            raise ValueError('invalid confidence weights')
        if (type(self.top_k) is not int or self.top_k < 1
            or type(self.effective_cutoff) not in (float, int)
            or not math.isfinite(self.effective_cutoff) or not 0 <= self.effective_cutoff <= 1
            or type(self.threshold) not in (float, int) or not math.isfinite(self.threshold)
            or not 0 <= self.threshold <= math.nextafter(1., math.inf)):
            raise ValueError('invalid confidence profile parameters')
        hashes = [self.retrieval_config_hash, *self.dataset_hashes.values()]
        if (not self.model_metadata or not self.dataset_hashes or
            any(not isinstance(h, str) or not re.fullmatch('[a-f0-9]{64}', h) for h in hashes)):
            raise ValueError('confidence profile provenance required')


@dataclass(frozen=True)
class EvidenceConfidence:
    passed: bool
    value: float
    top1: float | None
    gap: float
    effective_count: int
    missing_top2: bool
    reason_code: str | None
    reason: str


def validate_scores(scores):
    if any(type(s) not in (int, float) or not math.isfinite(s) or not 0 <= s <= 1 for s in scores):
        raise ValueError('reranker score missing, nonfinite or outside [0,1]')
    if any(a < b for a, b in itertools.pairwise(scores)):
        raise ValueError('reranker score order must preserve descending original rank')


def score_evidence(scores: Sequence[float], *, profile: ConfidenceProfile) -> EvidenceConfidence:
    validate_scores(scores)
    scores = scores[:profile.top_k]
    if not scores:
        return EvidenceConfidence(False, 0., None, 0., 0, True, 'no_evidence', '没有可回答的检索证据')
    top1 = scores[0]
    gap = top1 - scores[1] if len(scores) > 1 else 0.
    count = sum(s >= profile.effective_cutoff for s in scores)
    w_s, w_n, w_g = profile.weights
    value = w_s * top1 + w_n * count / profile.top_k + w_g * gap
    passed = value >= profile.threshold
    return EvidenceConfidence(passed, value, top1, gap, count, len(scores) < 2,
                              None if passed else 'low_relevance',
                              '证据通过校准置信闸' if passed else '检索证据置信度低于校准阈值')


def load_confidence_profile(path: Path, *, expected: dict) -> ConfidenceProfile:
    try:
        record = json.loads(path.read_text(encoding='utf-8'))
        profile = ConfidenceProfile(**record)
    except (OSError, TypeError, KeyError, ValueError) as exc:
        raise ValueError('confidence profile missing or invalid') from exc
    for key in ('top_k', 'model_metadata', 'dataset_hashes', 'retrieval_config_hash'):
        if key not in expected or getattr(profile, key) != expected[key]:
            raise ValueError(f'confidence profile does not match current {key}')
    return profile


def retrieval_config_hash():
    from mewhelp.ch05.evidence import retrieve_current_evidence
    root = Path(__file__).resolve().parents[1] / 'knowledge'
    sources = [root / name for name in ('retrieval.py', 'vectors.py', 'filters.py', 'reranking.py')]
    content = '\n'.join(p.read_text(encoding='utf-8').replace('\r\n', '\n') for p in sources)
    content += inspect.getsource(retrieve_current_evidence).replace('\r\n', '\n')
    return hashlib.sha256((content + 'original:hybrid:rrf60:candidates50:rank10:gate5').encode()).hexdigest()


def current_profile_identity(dataset: Path | None = None):
    from mewhelp.knowledge.evaluation.dataset import file_hash
    from mewhelp.knowledge.evaluation.runner import _model_metadata
    dataset = dataset or Path(__file__).resolve().parents[3] / 'eval/ch04'
    return {'top_k': 5, 'model_metadata': _model_metadata(),
                'dataset_hashes': {key: file_hash(dataset / name) for key, name in
                                [('corpus_hash', 'corpus.jsonl'), ('query_hash', 'queries.jsonl')]},
                'retrieval_config_hash': retrieval_config_hash()}


def load_workflow_confidence(settings):
    if settings is None or not settings.enabled:
        return None
    return load_confidence_profile(settings.confidence_path, expected=current_profile_identity())
