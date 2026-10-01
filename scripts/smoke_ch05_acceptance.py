"""Real JSON/SSE acceptance. Only an explicitly confirmed test offer writes a ticket."""

import argparse
import json
import re
from collections.abc import Callable
from pathlib import Path

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from mewhelp.ch05.workflow import CHITCHAT_REPLY
from mewhelp.db.models import Ticket
from mewhelp.knowledge.refusals import LowConfidenceQuestion

CASES = [
    {
        "id": "policy",
        "question": "七天无理由退货政策是什么",
        "expected": {
            "route": "knowledge",
            "nodes": ["retrieve_knowledge", "confidence_gate", "agent_decide"],
            "refused": False,
            "citations": True,
        },
    },
    {
        "id": "logistics",
        "question": "订单 1001 的物流到哪了",
        "expected": {
            "route": "business",
            "tools": ["query_logistics"],
            "forbidden_nodes": ["retrieve_knowledge", "confidence_gate"],
        },
    },
    {
        "id": "complaint",
        "question": "我要投诉",
        "expected": {
            "route": "complaint",
            "actions": ["handoff", "create_ticket"],
            "agent_calls": 0,
            "forbidden_nodes": ["agent_decide", "retrieve_knowledge"],
        },
    },
    {
        "id": "chitchat",
        "question": "你好",
        "expected": {
            "route": "chitchat",
            "answer": CHITCHAT_REPLY,
            "agent_calls": 0,
            "classifier_calls": 0,
        },
    },
    {
        "id": "complex",
        "question": "请先查询订单1001的商品和下单时间，再查询物流最新节点，比较这两个时间",
        "expected": {
            "route": "business",
            "sequential_tools": ["query_order", "query_logistics"],
            "forbidden_nodes": ["retrieve_knowledge", "confidence_gate"],
        },
    },
    {
        "id": "weak",
        "question": "今天店里新增的外星球旅行险承保条款是什么？",
        "expected": {
            "route": "knowledge",
            "nodes": ["retrieve_knowledge", "confidence_gate", "fallback_reply"],
            "refused": True,
            "agent_calls": 0,
            "forbidden_nodes": ["agent_decide", "stream_answer"],
        },
    },
]


def check_case(payload: dict, expected: dict) -> list[str]:
    failures = []
    for field in ("route", "refused", "answer"):
        if field in expected and payload.get(field) != expected[field]:
            failures.append(f"incorrect {field}")
    trace = payload.get("node_trace", [])
    previous = -1
    for node in expected.get("nodes", []):
        try:
            previous = trace.index(node, previous + 1)
        except ValueError:
            failures.append("required ordered node missing: " + node)
    for node in expected.get("forbidden_nodes", []):
        if node in trace:
            failures.append("unexpected node: " + node)
    calls = payload.get("calls", {})
    if "agent_calls" in expected and (
        calls.get("decision", -1) + calls.get("answer", -1) != expected["agent_calls"]
    ):
        failures.append("incorrect Agent call count")
    if "classifier_calls" in expected and calls.get("classifier") != expected["classifier_calls"]:
        failures.append("incorrect classifier call count")
    if "actions" in expected and set(payload.get("actions", [])) != set(expected["actions"]):
        failures.append("incorrect independent actions")
    tools = payload.get("tool_trace", [])
    for name in expected.get("tools", []):
        if not any(t.get("name") == name and t.get("ok") for t in tools):
            failures.append("successful tool missing: " + name)
    last_round = 0
    for name in expected.get("sequential_tools", []):
        found = next(
            (
                t
                for t in tools
                if t.get("name") == name and t.get("ok") and t.get("round", 0) > last_round
            ),
            None,
        )
        if found is None:
            failures.append("sequential observation-dependent tool missing: " + name)
        else:
            last_round = found["round"]
    if expected.get("citations"):
        cited = {int(n) for n in re.findall(r"\[([1-9][0-9]*)\]", payload.get("answer", ""))}
        known = {s["number"] for s in payload.get("sources", [])}
        if not cited or not cited <= known:
            failures.append("answer lacks mapped evidence citations")
    if "ticket_delta" in expected:
        before, after = payload.get("tickets_before"), payload.get("tickets_after")
        if before is None or after is None or after - before != expected["ticket_delta"]:
            failures.append("unexpected ticket write")
    if payload.get("ledger_error"):
        failures.append("business ledger did not commit")
    return failures


def completed_sse(raw: str) -> tuple[dict, list[dict]]:
    frames = []
    for block in re.split(r"\r?\n\r?\n", raw):
        name, data = "message", []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data:
            frames.append({"event": name, "data": json.loads("\n".join(data))})
    if any(f["event"] == "error" for f in frames):
        raise ValueError("SSE service error")
    done = [f["data"] for f in frames if f["event"] == "done"]
    if len(done) != 1:
        raise ValueError("SSE needs exactly one completion")
    tokens = "".join(f["data"]["text"] for f in frames if f["event"] == "token")
    if tokens != done[0]["answer"]:
        raise ValueError("SSE tokens differ from committed answer")
    if done[0]["route"] == "knowledge":
        events = [f["event"] for f in frames]
        if events.index("sources") > events.index("token"):
            raise ValueError("knowledge evidence must precede first answer token")
    return done[0], frames


def ticket_count(factory):
    with factory() as db:
        return db.scalar(select(func.count()).select_from(Ticket))


def committed_refusal(payload, case, factory, entry):
    with factory() as db:
        row = db.get(LowConfidenceQuestion, int(payload["low_confidence_question_id"]))
        if (
            row is None
            or row.original_question != case["question"]
            or row.source_conversation_id != payload["conversation_id"]
            or row.trigger_stage != "retrieval"
            or row.entry_point != entry
        ):
            raise ValueError("weak evidence question was not independently committed")
        return {
            "id": str(row.id),
            "question": row.original_question,
            "stage": row.trigger_stage,
            "entry_point": row.entry_point,
            "reason": row.reason,
        }


def run_acceptance(
    base_url: str, *, report_dir: Path, session_prefix: str, session_factory: Callable[[], Session]
) -> int:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", session_prefix):
        raise ValueError("use a unique session prefix of at most 40 safe characters")
    report_dir.mkdir(parents=True, exist_ok=False)
    rows = []
    first_count = ticket_count(session_factory)
    with httpx.Client(base_url=base_url, timeout=210) as client:
        for mode in ("json", "sse"):
            for case in CASES:
                row = {
                    "id": case["id"],
                    "mode": mode,
                    "question": case["question"],
                    "expected": case["expected"],
                }
                request = {
                    "message": case["question"],
                    "session_id": f"{session_prefix}-{mode}-{case['id']}",
                    "user_id": session_prefix,
                }
                row["request"] = request
                try:
                    response = client.post(
                        "/ch05/agent" if mode == "json" else "/ch05/chat/stream", json=request
                    )
                    row["raw"] = response.text
                    response.raise_for_status()
                    if mode == "sse":
                        payload, frames = completed_sse(response.text)
                        row["frames"] = frames
                    else:
                        payload = response.json()
                    row["actual"] = payload
                    row["failures"] = check_case(payload, case["expected"])
                    if case["id"] == "weak":
                        row["pool"] = committed_refusal(
                            payload,
                            case,
                            session_factory,
                            "agent" if mode == "json" else "chat_stream",
                        )
                    row["passed"] = not row["failures"]
                except Exception as exc:  # noqa: BLE001 -- errors fail and stay in the report
                    row.update(passed=False, error=f"{type(exc).__name__}: {exc}")
                rows.append(row)
                (report_dir / "cases.json").write_text(
                    json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(f"{mode}/{case['id']}: {'PASS' if row['passed'] else 'FAIL'}", flush=True)
        # Rendering suggestions and continuing conversation must not create a ticket.
        no_click_count = ticket_count(session_factory)
        counts = {"tickets_before": first_count, "tickets_after": no_click_count}
        row = {"id": "no-automatic-ticket", "actual": counts, "expected": {"ticket_delta": 0}}
        row["failures"] = check_case(counts, row["expected"])
        if any(not r["passed"] for r in rows):
            row["failures"].append(
                "incomplete dialogue cases cannot prove safe suggestion rendering"
            )
        row["passed"] = not row["failures"]
        rows.append(row)
        complaint = next(
            (
                r
                for r in rows
                if r["id"] == "complaint" and r.get("passed") and r.get("mode") == "json"
            ),
            None,
        )
        if complaint:
            row = {"id": "explicit-ticket", "expected": {"ticket_delta": 1}}
            try:
                payload = complaint["actual"]
                request = {
                    "session_id": payload["session_id"],
                    "user_id": session_prefix,
                    "offer_id": payload["offer"]["offer_id"],
                    "confirmed": True,
                    "description": session_prefix + " HTTP验收测试工单，请忽略。",
                    "ticket_type": "投诉",
                }
                row["request"] = request
                cancelled = client.post("/ch05/tickets", json={**request, "confirmed": False})
                row["cancel_status"] = cancelled.status_code
                if cancelled.status_code != 422 or ticket_count(session_factory) != no_click_count:
                    raise ValueError("unconfirmed request wrote a ticket or was accepted")
                receipt = client.post("/ch05/tickets", json=request).raise_for_status().json()
                replay = client.post("/ch05/tickets", json=request).raise_for_status().json()
                row["receipt"], row["replay"] = receipt, replay
                row["actual"] = {
                    "tickets_before": no_click_count,
                    "tickets_after": ticket_count(session_factory),
                }
                row["failures"] = check_case(row["actual"], row["expected"])
                if receipt["ticket_no"] != replay["ticket_no"] or not replay["replayed"]:
                    row["failures"].append("confirmed retry created another ticket")
                row["passed"] = not row["failures"]
            except Exception as exc:  # noqa: BLE001
                row.update(passed=False, error=f"{type(exc).__name__}: {exc}")
            rows.append(row)
    report = {
        "origin": "real HTTP + corresponding business database",
        "base_url": base_url,
        "session_prefix": session_prefix,
        "total": len(rows),
        "passed": sum(r["passed"] for r in rows),
        "service_errors": sum("error" in r for r in rows),
        "cases": rows,
    }
    (report_dir / "acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return int(report["passed"] != report["total"])


if __name__ == "__main__":
    from mewhelp.db.engine import SessionLocal

    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--session-prefix", required=True)
    args = parser.parse_args()
    raise SystemExit(
        run_acceptance(
            args.base_url,
            report_dir=args.report_dir,
            session_prefix=args.session_prefix,
            session_factory=SessionLocal,
        )
    )
