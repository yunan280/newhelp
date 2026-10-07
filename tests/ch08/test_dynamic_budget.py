import pytest

from mewhelp.ch07.budget import ContextBudgetError, compute_budget, measured_prefix
from mewhelp.ch07.config import BudgetProfile, ContextSettings


def test_dynamic_catalog_counts_full_schema():
    profile = BudgetProfile()
    small = measured_prefix(profile, tools=[])
    large = measured_prefix(profile, tools=[{'type': 'function', 'function': {'name': 'giant', 'description': '中文' * 16000, 'parameters': {'type': 'object'}}}])
    assert large > small + 1000
    with pytest.raises(ContextBudgetError):
        compute_budget(ContextSettings(), profile, actual_fixed={'prefix': large * 10})
