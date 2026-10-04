from dataclasses import dataclass

from .config import BudgetProfile, ContextSettings
from .tokens import estimate_request


class ContextBudgetError(ValueError):
    pass


@dataclass(frozen=True)
class ContextBudget:
    fixed: int
    peak: int
    available: int
    history: int
    layer1: int
    layer2: int
    breakdown: dict[str, int]
    profile_hash: str


def compute_budget(settings: ContextSettings, profile: BudgetProfile,
                   *, actual_fixed: dict[str, int] | None = None) -> ContextBudget:
    actual = actual_fixed or {}
    breakdown = {
        'prefix': max(profile.prefix_reserve, actual.get('prefix', 0)),
        'evidence': max(settings.rerank_top_k * profile.evidence_per_chunk,
                        actual.get('evidence', 0)),
        'summary': max(profile.summary_reserve, actual.get('summary', 0)),
        'output': settings.max_output_tokens,
        'safety': profile.safety_reserve,
        'current_user': settings.max_user_input_tokens,
        'tool_peak': settings.max_agent_steps * settings.tool_result_max_tokens,
        'control': profile.control_reserve,
    }
    fixed = sum(breakdown[key] for key in ('prefix', 'evidence', 'summary', 'output', 'safety'))
    peak = sum(breakdown[key] for key in ('current_user', 'tool_peak', 'control'))
    available = max(0, settings.model_context_window - fixed - peak)
    history = min(profile.desired_turns * profile.steady_turn, available)
    if history < profile.steady_turn:
        raise ContextBudgetError(f'上下文预算不足：历史仅 {history} token，'
                                 f'一轮需 {profile.steady_turn}；fixed={fixed}, peak={peak}')
    return ContextBudget(fixed, peak, available, history, max(0, 7 * history // 10 - 1),
                         3 * history // 10, breakdown, profile.fingerprint)


def check_window(messages, tools, *, settings: ContextSettings, profile: BudgetProfile,
                 output_tokens: int, remaining_tool_calls: int) -> None:
    if remaining_tool_calls < 0 or output_tokens < 0:
        raise ValueError('negative output/tool reservation')
    actual = estimate_request(messages, tools, profile=profile)
    reserved = (output_tokens + remaining_tool_calls * settings.tool_result_max_tokens
                + profile.control_reserve + profile.safety_reserve)
    if actual + reserved > settings.model_context_window:
        raise ContextBudgetError(f'上下文预算不足：请求 {actual} + 预留 {reserved} '
                                 f'> 窗口 {settings.model_context_window}')
