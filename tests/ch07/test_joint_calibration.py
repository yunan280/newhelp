from pathlib import Path

from mewhelp.ch07.budget import compute_budget, measured_prefix
from mewhelp.ch07.config import BudgetProfile, ContextSettings
from mewhelp.ch07.evaluation import case_messages, read_cases
from mewhelp.ch07.tokens import estimate_request


def test_approved_joint_profile_covers_frozen_real_measurements():
    profile = BudgetProfile()
    assert (profile.cjk_tokens_per_char, profile.ascii_chars_per_token,
            profile.prefix_reserve) == (1.2, 3, 1700)
    rows = read_cases(Path('artifacts/ch07/20261004-native/tokens-calibration-01/results.jsonl'))
    cases = read_cases(Path('eval/ch07/tokens-calibration.jsonl'))
    for case, measured in zip(cases, rows):
        assert estimate_request(case_messages(case['messages']), case['tools'], profile=profile) >= measured['usage']['input_tokens']
    demo = ContextSettings(model_context_window=18000, max_output_tokens=2000,
        max_user_input_tokens=2000, max_agent_steps=3, tool_result_max_tokens=1200,
        rerank_top_k=5, _env_file=None)
    budget = compute_budget(demo, profile)
    assert (budget.history, budget.layer1, budget.layer2) == (5300, 3709, 1590)
    # The Ch07 profile remains the historical floor; Ch08 measures each live catalog.
    actual_prefix = measured_prefix(profile)
    dynamic = compute_budget(demo, profile, actual_fixed={'prefix': actual_prefix})
    assert dynamic.breakdown['prefix'] >= actual_prefix
    assert dynamic.history <= budget.history
