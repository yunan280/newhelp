from mewhelp.ch06 import evaluation


def select(rows, has_small):
    function = getattr(evaluation, "select_router_thresholds", None)
    assert function is not None, "missing independently testable threshold selection"
    return function(rows, has_small=has_small)


def row(expected, primary, small=None):
    result = {"case": {"expected": {"intent": expected}}, "primary": {"actual": primary}}
    if small is not None:
        result["small"] = {"actual": small}
    return result


def test_core_leaks_take_priority_over_upgrade_cost():
    rows = [
        row(
            "退款退货",
            {"intent": "退款退货", "confidence": 0.99},
            {"intent": "订单", "confidence": 0.85},
        )
    ]
    selected, grid = select(rows, True)
    assert selected[0] == 0 and selected[2] == 1 and selected[4] == 0.9
    assert len(grid) == 12


def test_primary_threshold_balances_other_and_core_without_acceptance_labels():
    rows = [
        row("其他", {"intent": "退款退货", "confidence": 0.6}),
        row("退款退货", {"intent": "退款退货", "confidence": 0.7}),
    ]
    selected, _ = select(rows, False)
    assert selected[:3] == (0, 0, 0) and selected[3] == 0.7


def test_equal_accuracy_prefers_fewer_upgrades():
    rows = [
        row("订单", {"intent": "订单", "confidence": 0.99}, {"intent": "订单", "confidence": 0.85})
    ]
    selected, _ = select(rows, True)
    assert selected[:3] == (0, 0, 0) and selected[4] < 0.85
