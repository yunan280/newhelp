"""Shared conservative usage accounting and pre-request budget checks."""

import json
from dataclasses import dataclass

from pydantic import BaseModel, Field


@dataclass(frozen=True)
class AgentLimits:
    max_decisions: int = 4
    max_tools: int = 8
    final_max_tokens: int = 1024
    decision_max_tokens: int = 256
    classifier_max_tokens: int = 128
    total_model_tokens: int = 64000
    turn_seconds: float = 180
    request_seconds: float = 30
    max_retries: int = 0


class TokenUsage(BaseModel):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    estimated: bool = False

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def plus(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            estimated=self.estimated or other.estimated,
        )


class BudgetExceeded(Exception):
    pass


def estimate_call_tokens(messages: list[dict], tools: list[dict]) -> int:
    # UTF-8 bytes are a conservative upper bound, including serialization/schema overhead.
    return (
        len(
            json.dumps(
                {"messages": messages, "tools": tools}, ensure_ascii=False, default=str
            ).encode("utf-8")
        )
        + 128
    )


def reserve_call(
    used: int, input_bound: int, output_limit: int, final_reserve: int, limits: AgentLimits
) -> None:
    if used + input_bound + output_limit + final_reserve > limits.total_model_tokens:
        raise BudgetExceeded("turn token budget cannot accommodate this request")


def observed_usage(raw: dict | None, *, input_bound: int, output: str) -> TokenUsage:
    raw = raw or {}
    inp = raw.get("input_tokens", raw.get("prompt_tokens"))
    out = raw.get("output_tokens", raw.get("completion_tokens"))
    if inp is not None and out is not None:
        return TokenUsage(input_tokens=inp, output_tokens=out)
    return TokenUsage(
        input_tokens=input_bound, output_tokens=len(output.encode("utf-8")), estimated=True
    )


BOUNDED_REPLY = "本次查询已达到处理限额。请缩小问题范围，或选择转人工协助处理。"
