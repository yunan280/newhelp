"""The framework-free loop: ask, execute tools, feed observations back, repeat."""

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
from langchain_core.utils.function_calling import convert_to_openai_tool

from mewhelp.config import get_settings
from mewhelp.tools.business import build_business_tools
from mewhelp.tools.registry import ToolRegistry, ToolSpec

from .limits import (
    BOUNDED_REPLY,
    AgentLimits,
    BudgetExceeded,
    TokenUsage,
    estimate_call_tokens,
    observed_usage,
    reserve_call,
)


@dataclass
class BareResult:
    answer: str
    model_calls: int
    tool_trace: list[dict]
    usage: TokenUsage
    stop_reason: str


def tool_schemas(registry: ToolRegistry) -> list[dict]:
    return [convert_to_openai_tool(t) for t in registry.tools()]


async def complete_http(
    client: httpx.AsyncClient, messages: list[dict], tools: list[dict], max_tokens: int
) -> dict:
    settings = get_settings()
    response = await client.post(
        settings.openai_base_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {settings.openai_api_key}"},
        json={
            "model": settings.llm_model,
            "messages": messages,
            "tools": tools,
            "max_tokens": max_tokens,
            "thinking": {"type": "disabled"},
            "temperature": 0,
        },
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


async def bare_loop(
    question: str, *, complete, registry: ToolRegistry, limits: AgentLimits
) -> BareResult:
    messages = [
        {"role": "system", "content": "你是客服小猫。缺少参数就追问；按工具结果回答。"},
        {"role": "user", "content": question},
    ]
    schemas = tool_schemas(registry)
    usage = TokenUsage()
    trace, seen = [], set()
    calls = 0
    deadline = time.monotonic() + limits.turn_seconds
    stop = "decision_limit"
    while calls < limits.max_decisions:
        if time.monotonic() >= deadline:
            stop = "deadline"
            break
        estimate = estimate_call_tokens(messages, schemas)
        try:
            reserve_call(usage.total, estimate, limits.final_max_tokens, 0, limits)
        except BudgetExceeded:
            stop = "token_budget"
            break
        raw = await asyncio.wait_for(
            complete(messages, schemas, limits.final_max_tokens),
            min(limits.request_seconds, deadline - time.monotonic()),
        )
        calls += 1
        message = raw["choices"][0]["message"]
        usage = usage.plus(
            observed_usage(
                raw.get("usage"),
                input_bound=estimate,
                output=json.dumps(message, ensure_ascii=False),
            )
        )
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return BareResult(message.get("content") or "", calls, trace, usage, "completed")
        if len(trace) + len(tool_calls) > limits.max_tools:
            stop = "tool_limit"
            break
        messages.append(message)
        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                args = json.loads(tc["function"]["arguments"])
                if not isinstance(args, dict):
                    raise TypeError("tool arguments must be an object")
            except (ValueError, TypeError):
                args = {}
            key = (name, json.dumps(args, sort_keys=True))
            if key in seen:
                return BareResult(BOUNDED_REPLY, calls, trace, usage, "no_progress")
            seen.add(key)
            result = await registry.run(name, args)
            trace.append(
                {
                    "call_id": tc["id"],
                    "round": calls,
                    "name": name,
                    "args": args,
                    "ok": result.ok,
                    "content": result.content,
                    "error": result.error,
                    "elapsed_ms": result.elapsed_ms,
                }
            )
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result.content})
    return BareResult(BOUNDED_REPLY, calls, trace, usage, stop)


async def _demo(question: str, out: Path | None) -> None:
    registry = ToolRegistry({t.name: ToolSpec(t) for t in build_business_tools()})
    responses = []
    async with httpx.AsyncClient() as client:

        async def complete(messages, tools, max_tokens):
            raw = await complete_http(client, messages, tools, max_tokens)
            responses.append(raw.get("model"))
            return raw

        result = await bare_loop(
            question, complete=complete, registry=registry, limits=AgentLimits()
        )
    payload = asdict(result)
    payload["usage"] = result.usage.model_dump()
    payload.update(request_model=get_settings().llm_model, response_models=responses)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--question", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    asyncio.run(_demo(args.question, args.out))
