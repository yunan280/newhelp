"""Pre-labeled mixed business/knowledge HTTP check; isolated instance only."""

import datetime as dt
import json
from pathlib import Path

import httpx
from sqlalchemy import select

from mewhelp.db.models import Conversation
from scripts.smoke_ch04_acceptance import _parse_sse, _verify_refusal, sqlite_factory

OUT = Path(__file__).resolve().parent
INPUT = {
    "question": "订单1001的状态和HX-210的蓝牙版本？",
    "expected": "refusal",
    "basis": "隔离知识有商品蓝牙参数，但没有订单1001状态；必须按整轮证据不足拒答，不能用单个业务结果绕过知识门控。",
    "entries": ["agent", "chat_stream"],
}


def main():
    labels = OUT / "mixed-acceptance-input.json"
    if labels.exists():
        assert json.loads(labels.read_text(encoding="utf-8")) == INPUT
    else:
        labels.write_text(json.dumps(INPUT, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    factory = sqlite_factory(Path("artifacts/ch04/acceptance_ch04_20260930_03/acceptance.sqlite"))
    started = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    report = {
        "scope": "isolated_mixed_business_knowledge",
        "input": INPUT,
        "records": [],
        "passed": False,
    }
    try:
        with httpx.Client(base_url="http://127.0.0.1:8001", timeout=300) as client:
            for entry in INPUT["entries"]:
                path = "/ch02/agent" if entry == "agent" else "/ch02/chat/stream"
                response = client.post(
                    path, json={"user_id": "ch04-mixed-smoke", "message": INPUT["question"]}
                )
                response.raise_for_status()
                if entry == "agent":
                    payload = response.json()
                    names = None
                else:
                    frames = _parse_sse(response.text)
                    names = [name for name, _ in frames]
                    assert names[0] == "session" and names[-1] == "done" and "error" not in names
                    assert names.count("sources") == 1 and names.index("sources") < names.index(
                        "token"
                    )
                    session = next(data for name, data in frames if name == "session")
                    sources = next(data for name, data in frames if name == "sources")
                    with factory() as db:
                        conversation_id = db.scalar(
                            select(Conversation.id).where(
                                Conversation.session_id == session["session_id"]
                            )
                        )
                    assert conversation_id is not None
                    payload = {
                        **session,
                        **sources,
                        "conversation_id": conversation_id,
                        "answer": "".join(data["text"] for name, data in frames if name == "token"),
                    }
                row = {"entry": entry, "payload": payload, "sse_events": names}
                report["records"].append(row)
                row["pool"] = _verify_refusal(payload, factory, INPUT["question"], entry, started)
                print(entry, "committed refusal", row["pool"]["reason_code"], flush=True)
        report["passed"] = True
    except Exception as exc:  # noqa: BLE001 — save failed acceptance and exit nonzero
        report["error"] = f"{type(exc).__name__}: {exc}"
    (OUT / "mixed-acceptance.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
