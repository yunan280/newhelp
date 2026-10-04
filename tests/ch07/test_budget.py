from importlib import import_module

import pytest
from langchain_core.messages import HumanMessage


def modules():
    try:
        return import_module('mewhelp.ch07.config'), import_module('mewhelp.ch07.budget')
    except ModuleNotFoundError:
        pytest.fail('Ch07 must derive the history budget instead of a fixed 2048')


def demo(config, **overrides):
    values = {'model_context_window':18000, 'max_output_tokens':2000,
              'max_user_input_tokens':2000, 'max_agent_steps':3,
              'tool_result_max_tokens':1200, 'rerank_top_k':5, '_env_file':None}
    return config.ContextSettings(**{**values, **overrides})


def test_demo_budget_is_derived():
    config, budget = modules()
    result = budget.compute_budget(demo(config), config.BudgetProfile())
    assert (result.fixed, result.peak, result.history, result.layer1, result.layer2) == (
        6700, 6000, 5300, 3709, 1590)
    assert budget.compute_budget(demo(config, model_context_window=19000),
                                 config.BudgetProfile()).history == 6300


def test_default_budget_preserves_target_pool(monkeypatch):
    config, budget = modules()
    for key in ('MODEL_CONTEXT_WINDOW', 'MAX_OUTPUT_TOKENS', 'MAX_USER_INPUT_TOKENS',
                'MAX_AGENT_STEPS', 'TOOL_RESULT_MAX_TOKENS', 'RERANK_TOP_K'):
        monkeypatch.delenv(key, raising=False)
    result = budget.compute_budget(config.ContextSettings(_env_file=None), config.BudgetProfile())
    assert (result.available, result.history, result.layer1, result.layer2) == (
        107908, 42560, 29791, 12768)


def test_one_steady_turn_cannot_fit_raises():
    config, budget = modules()
    with pytest.raises(budget.ContextBudgetError, match='上下文预算不足'):
        budget.compute_budget(demo(config, model_context_window=13000), config.BudgetProfile())


def test_actual_evidence_over_reserve_reduces_history():
    config, budget = modules()
    result = budget.compute_budget(demo(config), config.BudgetProfile(),
                                   actual_fixed={'evidence': 3000})
    assert result.history == 4300


def test_remaining_parallel_calls_reserve_each_result():
    config, budget = modules()
    settings = demo(config, model_context_window=18000)
    messages = [HumanMessage('中' * 11000)]
    budget.check_window(messages, [], settings=settings, profile=config.BudgetProfile(),
                        output_tokens=2000, remaining_tool_calls=1)
    with pytest.raises(budget.ContextBudgetError, match='上下文预算不足'):
        budget.check_window(messages, [], settings=settings, profile=config.BudgetProfile(),
                            output_tokens=2000, remaining_tool_calls=3)


def test_profile_counter_drift_is_rejected(tmp_path):
    config, _ = modules()
    profile = config.BudgetProfile()
    path = tmp_path / 'calibration.json'
    path.write_text('{"profile": {"cjk_tokens_per_char": 2}, "profile_hash": "old"}')
    with pytest.raises(ValueError, match='profile'):
        config.load_profile(path)
    assert profile.fingerprint != config.BudgetProfile(cjk_tokens_per_char=2).fingerprint


def test_unprefixed_environment(monkeypatch):
    config, _budget = modules()
    monkeypatch.setenv('MODEL_CONTEXT_WINDOW', '19000')
    assert config.ContextSettings(_env_file=None).model_context_window == 19000
