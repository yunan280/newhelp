import pytest


def test_metrics_use_actual_relevant_sets():
    from mewhelp.knowledge.evaluation.metrics import faithfulness, recall_at_k, reciprocal_rank_at_k

    assert recall_at_k([9, 2, 3], {2, 3}, 2) == 0.5
    assert reciprocal_rank_at_k([9, 2, 3], {2, 3}, 3) == 0.5
    assert recall_at_k([9], {2}, 50) == 0.0
    assert reciprocal_rank_at_k([9], {2}, 50) == 0.0
    assert recall_at_k([9], set(), 50) is None
    assert reciprocal_rank_at_k([9], set(), 50) is None
    assert faithfulness([True, False, True]) == pytest.approx(2 / 3)
    assert faithfulness([]) is None


def test_duplicate_hits_do_not_inflate_recall_or_rank():
    from mewhelp.knowledge.evaluation.metrics import recall_at_k, reciprocal_rank_at_k

    assert recall_at_k([2, 2, 3], {2, 3}, 2) == 1.0
    assert reciprocal_rank_at_k([9, 9, 2], {2}, 2) == 0.5


@pytest.mark.parametrize("k", [0, -1, True, 1.5])
def test_invalid_cutoffs_rejected(k):
    from mewhelp.knowledge.evaluation.metrics import recall_at_k

    with pytest.raises(ValueError):
        recall_at_k([2], {2}, k)


def test_claim_labels_must_be_boolean():
    from mewhelp.knowledge.evaluation.metrics import faithfulness

    with pytest.raises(ValueError):
        faithfulness([1])
