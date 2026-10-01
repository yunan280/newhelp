"""Read the actual acceptance checkpoint twice, and verify a restarted HTTP process.

Run after starting the same server with the same checkpoint file. Does not reset data.
"""
import asyncio
import json
from pathlib import Path

import httpx
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from mewhelp.ch05.workflow import build_workflow

CHECKPOINT = "data/ch05/ch05_20261001_01-ui.sqlite3"
SESSION = "ch05_20261001_01c-json-complex"
OTHER = "ch05_20261001_01c-sse-complex"
USER = "ch05_20261001_01c"
OUT = Path(__file__).with_name("process-recovery.json")


async def snapshots():
    async with AsyncSqliteSaver.from_conn_string(CHECKPOINT) as saver:
        graph = build_workflow(saver)
        saved = {}
        for name in (SESSION, OTHER, USER + "-unused"):
            snapshot = await graph.aget_state({"configurable": {"thread_id": name}})
            state = snapshot.values
            saved[name] = {
                "message_ids": [m.id for m in state.get("messages", [])],
                "history_count": len(state.get("messages", [])),
                "response_models": sorted({m.response_metadata["model_name"]
                    for m in state.get("agent_messages", [])
                    if getattr(m, "response_metadata", {}).get("model_name")}),
                "tool_rounds": [(t["name"], t["round"]) for t in state.get("tool_trace", [])],
            }
        return saved


async def main():
    first = await snapshots()
    reopened = await snapshots()
    assert first == reopened
    assert first[SESSION]["history_count"] == 2
    assert first[OTHER]["history_count"] == 2
    assert first[USER + "-unused"]["history_count"] == 0
    assert set(first[SESSION]["message_ids"]).isdisjoint(first[OTHER]["message_ids"])
    with httpx.Client(base_url="http://127.0.0.1:8005", timeout=210) as client:
        resumed = client.post("/ch05/agent", json={"session_id": SESSION, "user_id": USER,
                                                 "message": "你好"}).raise_for_status().json()
        assert resumed["resumed"] and resumed["calls"] == {"classifier": 0, "decision": 0, "answer": 0}
        # A knowledge refusal verifies logging without spending Agent answer tokens.
        logged = client.post("/ch05/agent", json={"session_id": USER + "-restart-policy",
                         "user_id": USER, "message": "今天店里新增的外星球旅行险承保条款是什么？"}).raise_for_status().json()
        assert logged["refused"] and "retrieve_knowledge" in logged["node_trace"]
    after = await snapshots()
    assert after[SESSION]["history_count"] == 4
    assert after[SESSION]["message_ids"][:2] == first[SESSION]["message_ids"]
    assert after[OTHER] == first[OTHER]
    OUT.write_text(json.dumps({"checkpoint": CHECKPOINT, "before": first, "reopened": reopened,
                 "after_restart_request": after, "resumed": resumed, "logged_policy": logged,
                 "passed": True}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Closed/reopened official saver + restarted HTTP process + session isolation: PASS")


asyncio.run(main())
