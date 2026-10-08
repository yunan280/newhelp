import json
import math
from dataclasses import asdict, replace

import pytest

from mewhelp.ch09.confidence import (
    ConfidenceProfile,
    load_confidence_profile,
    score_evidence,
)


def profile(**overrides):
    values = {'top_k': 5, 'effective_cutoff': .6, 'weights': (.5, .25, .25),
                  'threshold': .5, 'model_metadata': {'revision': 'actual-revision'},
                  'dataset_hashes': {'corpus_hash': 'a' * 64, 'query_hash': 'b' * 64},
                  'retrieval_config_hash': 'c' * 64}
    return ConfidenceProfile(**(values | overrides))


def test_ranked_signals_follow_calibrated_formula():
    result = score_evidence([.8, .6], profile=profile())
    assert result.value == pytest.approx(.55)
    assert (result.top1, result.gap, result.effective_count) == pytest.approx((.8, .2, 2))
    assert result.passed and not result.missing_top2


def test_single_evidence_does_not_invent_a_gap():
    result = score_evidence([.9], profile=profile())
    assert result.gap == 0 and result.missing_top2
    assert result.value == pytest.approx(.5)
    assert result.passed


def test_empty_evidence_refuses_even_at_zero_threshold():
    result = score_evidence([], profile=profile(threshold=0))
    assert not result.passed and result.reason_code == 'no_evidence'
    assert result.top1 is None and result.effective_count == 0


@pytest.mark.parametrize('scores', [[None], [math.nan], [math.inf], [-.1], [1.1], [.4, .8]])
def test_invalid_or_unsorted_scores_are_configuration_errors(scores):
    with pytest.raises(ValueError, match='score'):
        score_evidence(scores, profile=profile())


@pytest.mark.parametrize('weights', [(-.25, 1, .25), (.5, .5, .5), (.6, .2, .2), (.25, .5, .25)])
def test_invalid_weight_candidates_cannot_be_loaded(weights):
    with pytest.raises(ValueError, match='weight'):
        profile(weights=weights)


def test_only_top_k_in_original_rank_affects_confidence():
    assert score_evidence([.9, .8, .7, .6, .5, .4], profile=profile()) == score_evidence(
        [.9, .8, .7, .6, .5], profile=profile())


def test_profile_is_bound_to_models_dataset_and_retrieval_not_live_faqs(tmp_path):
    path = tmp_path / 'confidence.json'
    current = profile()
    path.write_text(json.dumps(asdict(current)), encoding='utf-8')
    identity = {key: getattr(current, key) for key in
                ('top_k', 'model_metadata', 'dataset_hashes', 'retrieval_config_hash')}
    assert load_confidence_profile(path, expected=identity | {'online_corpus_hash': 'changed'}) == current
    for key, value in [('top_k', 10), ('model_metadata', {'revision': 'new'}),
                       ('retrieval_config_hash', 'd' * 64),
                       ('dataset_hashes', {'corpus_hash': 'e' * 64, 'query_hash': 'b' * 64})]:
        with pytest.raises(ValueError, match='match'):
            load_confidence_profile(path, expected=identity | {key: value})


def test_high_threshold_all_refusal_profile_is_valid():
    assert not score_evidence([1.0], profile=replace(profile(), threshold=math.nextafter(1., math.inf))).passed


def test_disabled_workflow_does_not_load_models(monkeypatch):
    from mewhelp.ch09 import confidence
    from mewhelp.ch09.config import Ch09Settings
    monkeypatch.setattr(confidence, 'current_profile_identity', lambda: pytest.fail('disabled loads models'))
    assert confidence.load_workflow_confidence(Ch09Settings(enabled=False)) is None
