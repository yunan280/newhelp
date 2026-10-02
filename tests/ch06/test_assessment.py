from importlib import import_module

import pytest


async def test_assessment_requires_order_and_passed_policy_gate(core_runtime):
    try:
        assess = import_module("mewhelp.ch06.assessment").assess_order
    except ImportError:
        pytest.fail("missing fixed-order assessment")
    for state in [{}, {"order": {}, "gate": {"passed": False}}]:
        with pytest.raises(ValueError, match="order|gate"):
            await assess(state, core_runtime.context)
