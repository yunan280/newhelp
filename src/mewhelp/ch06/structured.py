"""Bounded application-validated JSON; all attempts share the turn's ledger."""

import asyncio
import json
import time
from dataclasses import dataclass

from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, ValidationError

from mewhelp.ch05.limits import (
    BudgetExceeded,
    TokenUsage,
    estimate_call_tokens,
    observed_usage,
    reserve_call,
)
from mewhelp.ch05.state import WorkflowContext

from .config import get_router_model


@dataclass
class StructuredCall:
    parsed: BaseModel | None
    error: str | None
    raw_responses: list[dict]
    usage: TokenUsage
    calls: dict[str, int]

    def patch(self) -> dict:
        return {"usage": self.usage.model_dump(), "calls": self.calls}


async def invoke_json(
    context: WorkflowContext,
    state: dict,
    *,
    purpose: str,
    messages: list,
    schema: type[BaseModel],
    model_name: str,
    output_tokens: int,
) -> StructuredCall:
    usage = TokenUsage.model_validate(state.get("usage", {}))
    calls = dict(state.get("calls", {}))
    raw_responses: list[dict] = []
    inputs = list(messages)
    result = StructuredCall(None, None, raw_responses, usage, calls)
    for attempt in range(2):
        serialized = [{"role": message.type, "content": message.content} for message in inputs]
        input_bound = estimate_call_tokens(serialized, [])
        try:
            reserve_call(
                result.usage.total,
                input_bound,
                output_tokens,
                context.limits.final_max_tokens,
                context.limits,
            )
        except BudgetExceeded:
            if not raw_responses:
                raise
            result.error = "token_budget"
            return result
        remaining = context.limits.turn_seconds - (
            time.time() - state.get("started_at", time.time())
        )
        if remaining <= 0:
            if not raw_responses:
                raise TimeoutError("turn deadline exceeded")
            result.error = "deadline"
            return result
        model = (context.router_model_factory or get_router_model)(
            purpose=purpose,
            model_name=model_name,
            output_tokens=output_tokens,
            request_seconds=context.limits.request_seconds,
        )
        started = time.perf_counter()
        # Provider exceptions are deliberately propagated, never disguised as a label.
        response = await asyncio.wait_for(
            model.ainvoke(inputs), timeout=min(remaining, context.limits.request_seconds)
        )
        text = (
            response.content
            if isinstance(response.content, str)
            else json.dumps(response.content, ensure_ascii=False)
        )
        observed = observed_usage(response.usage_metadata, input_bound=input_bound, output=text)
        result.usage = result.usage.plus(observed)
        calls[purpose] = calls.get(purpose, 0) + 1
        raw = {
            "content": text,
            "request_model": model_name,
            "response_model": response.response_metadata.get(
                "model_name", response.response_metadata.get("model")
            ),
            "usage": observed.model_dump(),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "parsed": False,
            "repair": bool(attempt),
        }
        raw_responses.append(raw)
        try:
            if response.tool_calls:
                raise ValueError("tools are forbidden in router control")
            result.parsed = schema.model_validate_json(text)
            raw["parsed"] = True
            result.error = None
            return result
        except (ValidationError, ValueError) as error:
            raw["validation_error"] = str(error)
            result.error = "invalid_json"
            inputs = inputs + [
                AIMessage(content=text),
                HumanMessage(
                    content=(
                        "上次输出不符合契约。只返回一个 JSON 对象，禁止代码围栏、解释和工具。严格遵守下面 schema：\n"
                        + json.dumps(schema.model_json_schema(), ensure_ascii=False)
                    )
                ),
            ]
    return result
