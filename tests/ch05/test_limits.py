import pytest


def api():
    try:
        from mewhelp.ch05 import limits
    except ImportError:
        pytest.fail("shared token budget is not implemented")
    return limits


def test_reserve_includes_final_response():
    limits = api()
    with pytest.raises(limits.BudgetExceeded):
        limits.reserve_call(20, 30, 40, 20, limits.AgentLimits(total_model_tokens=100))
    limits.reserve_call(20, 30, 40, 10, limits.AgentLimits(total_model_tokens=100))


def test_estimate_counts_utf8_and_tool_schema():
    limits = api()
    estimate = limits.estimate_call_tokens(
        [{"role": "user", "content": "退货政策"}], [{"name": "query_order", "parameters": {}}]
    )
    assert estimate > len("退货政策".encode())
