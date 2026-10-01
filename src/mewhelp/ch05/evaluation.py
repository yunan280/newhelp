"""Frozen labels, real model outputs and nonzero failure status."""

import argparse
import asyncio
import hashlib
import json
import re
import time
from pathlib import Path

from mewhelp.config import get_settings

from .config import get_ch05_model
from .intent import classify_intent
from .limits import AgentLimits, TokenUsage
from .state import WorkflowContext


def evaluation_hash(dataset: Path) -> str:
    digest = hashlib.sha256(dataset.read_bytes())
    names = ["prompts.py", "intent.py", "config.py"]
    if dataset.name == "agent-decisions.jsonl":
        names.append("agent.py")
    for name in names:
        digest.update(Path(__file__).with_name(name).read_bytes())
    digest.update(get_settings().llm_model.encode())
    return digest.hexdigest()


def check_decision(actual: dict, expected: dict) -> list[str]:
    failures = []
    if "tool" in expected:
        if not any(
            t["name"] == expected["tool"] and t["args"] == expected["args"]
            for t in actual.get("tools", [])
        ):
            failures.append("required tool and arguments missing")
    else:
        if actual.get("tools"):
            failures.append("unexpected tool call")
        if actual.get("reply_mode") != expected["reply_mode"]:
            failures.append("incorrect answer / clarification mode")
        if set(actual.get("actions", [])) != set(expected["actions"]):
            failures.append("incorrect optional actions")
    return failures


async def evaluate_decision_case(case: dict) -> dict:
    from langchain_core.messages import AIMessage, ToolMessage

    from .agent import (
        decide_agent,
        execute_agent_tools,
        next_agent_step,
        prompt_messages,
        stream_answer,
    )

    state = {
        "question": case["question"],
        "resolved_question": case["question"],
        "messages": [],
        "agent_messages": [],
        "pending_tool_calls": [],
        "tool_trace": [],
        "tool_count": 0,
        "decision_count": 0,
        "usage": TokenUsage().model_dump(),
        "calls": {"classifier": 0, "decision": 0, "answer": 0},
        "actions": [],
        "stop_reason": "",
        "started_at": time.time(),
        "evidence": None,
    }
    if case.get("evidence"):
        state["evidence"] = {
            "sources": [{"number": 1, "questions": "政策", "answer": case["evidence"]}]
        }
    state["agent_messages"] = prompt_messages(state)
    for i, item in enumerate(case["observations"]):
        cid = f"observation-{i}"
        state["agent_messages"].extend(
            [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": item["name"], "args": item["args"], "id": cid, "type": "tool_call"}
                    ],
                ),
                ToolMessage(content=item["content"], tool_call_id=cid),
            ]
        )
    raw_responses = []

    class RecordingModel:
        def __init__(self, model):
            self.model = model

        def bind_tools(self, tools):
            return RecordingModel(self.model.bind_tools(tools))

        async def ainvoke(self, messages):
            result = await self.model.ainvoke(messages)
            raw_responses.append(result.model_dump())
            return result

        async def astream(self, messages):
            async for chunk in self.model.astream(messages):
                raw_responses.append(chunk.model_dump())
                yield chunk

    ctx = WorkflowContext(
        lambda: None,
        lambda *a, **kw: RecordingModel(get_ch05_model(*a, **kw)),
        lambda: None,
        AgentLimits(),
    )
    state.update(await decide_agent(state, ctx))
    actual = {
        "tools": state["pending_tool_calls"],
        "reply_mode": (state.get("decision") or {}).get("reply_mode"),
        "actions": state["actions"],
    }
    failures = check_decision(actual, case["expected"])
    while next_agent_step(state) == "execute_tools":
        state.update(await execute_agent_tools(state, ctx, lambda event: None))
        state.update(await decide_agent(state, ctx))
    if next_agent_step(state) == "stream_answer":
        state.update(await stream_answer(state, ctx, lambda event: None))
    else:
        failures.append("unexpected bounded stop in labeled case")
    # A narrow guard complements manual reading of saved final answers; no wording snapshots.
    if re.search(r"已(?:经)?(?:为您)?(?:转接人工|转交人工|创建工单|批准退款)", state["answer"]):
        failures.append("reply claims a business side effect")
    return {
        "actual": actual,
        "raw": raw_responses,
        "answer": state["answer"],
        "tool_trace": state["tool_trace"],
        "usage": state["usage"],
        "calls": state["calls"],
        "response_models": list(
            dict.fromkeys(
                r["response_metadata"]["model_name"]
                for r in raw_responses
                if r["response_metadata"].get("model_name")
            )
        ),
        "failures": failures,
        "passed": not failures,
    }


async def evaluate_prompts(
    dataset_dir: Path, outdir: Path, *, part: str, classifier=classify_intent
) -> int:
    if part == "all":
        first = await evaluate_prompts(dataset_dir, outdir / "intents", part="intents")
        second = await evaluate_prompts(dataset_dir, outdir / "decisions", part="decisions")
        return max(first, second)
    outdir.mkdir(parents=True, exist_ok=False)
    dataset = dataset_dir / ("intents.jsonl" if part == "intents" else "agent-decisions.jsonl")
    model = get_ch05_model(128)
    results = []
    for line in dataset.read_text(encoding="utf-8").splitlines():
        case = json.loads(line)
        row = {**case, "request_model": get_settings().llm_model}
        try:
            if part == "decisions":
                row.update(await evaluate_decision_case(case))
            else:
                actual = await classifier(case["question"], model=model)
                row.update(
                    actual=actual.intent,
                    raw=actual.raw,
                    origin=actual.origin,
                    response_model=actual.response_model,
                    usage=actual.usage.model_dump(),
                    calls=0 if actual.origin == "local" else 1,
                    passed=actual.intent == case["expected"],
                )
        except Exception as exc:  # noqa: BLE001 -- every provider failure is a failed eval case
            row.update(passed=False, error=f"{type(exc).__name__}: {exc}")
        results.append(row)
        (outdir / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    summary = {
        "part": part,
        "hash": evaluation_hash(dataset),
        "total": len(results),
        "passed": sum(r["passed"] for r in results),
        "service_errors": sum("error" in r for r in results),
    }
    (outdir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary))
    return int(summary["passed"] != summary["total"] or not results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    parser.add_argument("--part", choices=["intents", "decisions", "all"], required=True)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(evaluate_prompts(args.dataset, args.outdir, part=args.part)))
