"""Real HTTP acceptance and a single-worker isolated instance of mewhelp.main.app."""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mewhelp.ch05.schemas import RefundReceipt, TurnResult


def validate_turn(payload, *, intent=None, core=False, waiting=False, weak=False):
    result = TurnResult.model_validate(payload)
    if intent is not None and result.intent != intent:
        raise ValueError(f"intent mismatch: {result.intent} != {intent}")
    if result.ledger_error or not result.answer.strip():
        raise ValueError("missing answer or failed ledger")
    if waiting:
        if result.status != "waiting_for_order" or result.order_selection is None:
            raise ValueError("missing waiting terminal/selector")
        if result.order is not None or result.assessment is not None:
            raise ValueError("waiting turn already assessed an order")
        return payload
    if result.status != "completed":
        raise ValueError("waiting result is not a completed turn")
    if weak:
        if not result.refused or result.assessment or "assess_order" in result.node_trace:
            raise ValueError("weak policy bypassed evidence gate")
    elif result.stop_reason != "completed":
        raise ValueError(f"bounded stop: {result.stop_reason}")
    if core:
        sequence = ["load_order", "expand_queries", "retrieve_policy", "confidence_gate"]
        if not weak:
            sequence += ["assess_order"]
        trace = result.node_trace
        positions = [trace.index(node) if node in trace else -1 for node in sequence]
        if -1 in positions or positions != sorted(positions):
            raise ValueError("incorrect core node sequence")
        if result.order is None or (not weak and (not result.assessment or not result.sources)):
            raise ValueError("core order/assessment/policy evidence missing")
    return payload


def sse_result(text):
    events = []
    for block in re.split(r"\r?\n\r?\n", text):
        name, data = "message", []
        for line in block.splitlines():
            if line.startswith("event:"):
                name = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if data:
            events.append({"event": name, "data": json.loads("\n".join(data))})
    if any(event["event"] == "error" for event in events):
        raise ValueError("SSE error: " + json.dumps(events[-1], ensure_ascii=False))
    terminals = [event for event in events if event["event"] in {"done", "waiting_for_order"}]
    if len(terminals) != 1 or events[-1] is not terminals[0]:
        raise ValueError("missing or ambiguous SSE terminal")
    payload = dict(terminals[0]["data"])
    payload.pop("finish_reason", None)
    if terminals[0]["event"] == "waiting_for_order":
        selectors = [event["data"] for event in events if event["event"] == "order_selection"]
        if len(selectors) != 1 or selectors[0] != payload.get("order_selection"):
            raise ValueError("waiting selector/terminal mismatch")
    elif payload.get("route") == "aftersales" and payload.get("sources"):
        names = [event["event"] for event in events]
        if "sources" not in names or "token" not in names or names.index("sources") > names.index("token"):
            raise ValueError("policy sources must precede answer tokens")
    return payload


def validate_receipt(payload, offer):
    receipt = RefundReceipt.model_validate(payload)
    expected = "RF" + hashlib.sha256(offer["offer_id"].encode()).hexdigest()[:30]
    if receipt.application_no != expected or receipt.order_id != offer["order"]["order_id"]:
        raise ValueError("incorrect application identity")
    return payload


def request_case(client, report, name, method, path, *, json=None, validator=None,
                 sse=False, expected_status=200):
    row = {"name": name, "method": method, "path": path, "request": json, "passed": False}
    report["cases"].append(row)
    try:
        response = client.request(method, path, json=json)
        row["status"] = response.status_code
        row["response"] = response.text if sse else response.json()
        if response.status_code != expected_status:
            row["service_error"] = response.status_code >= 500
            raise ValueError(f"HTTP {response.status_code}, expected {expected_status}")
        payload = sse_result(response.text) if sse else row["response"]
        if validator is not None:
            validator(payload)
        row["result"] = payload
        row["passed"] = True
        return payload
    except Exception as error:  # noqa: BLE001 - preserve every failed case in denominator
        row["error"] = type(error).__name__ + ": " + str(error)[:1000]
        row["service_error"] = row.get("service_error", not isinstance(error, ValueError))
        return None


def save_report(report, path):
    report.update(total=len(report["cases"]), passed=sum(row["passed"] for row in report["cases"]),
                  service_errors=sum(bool(row.get("service_error")) for row in report["cases"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return int(not report["total"] or report["passed"] != report["total"])


def run_acceptance(client, report, prefix):
    user = prefix + "-user"

    def turn(name, message, *, session=None, intent=None, core=False, waiting=False,
             filters=None, weak=False, sse=False):
        payload = {"user_id": user, "session_id": session or prefix + "-" + name, "message": message}
        if filters:
            payload["filters"] = filters
        return request_case(client, report, name, "POST", "/ch05/chat/stream" if sse else "/ch05/agent",
                            json=payload, sse=sse, validator=lambda data: validate_turn(
                                data, intent=intent, core=core, waiting=waiting, weak=weak))

    faq = turn("faq", "退货政策是什么", intent="退款退货")
    if faq and (faq["route"] != "knowledge" or any(node in faq["node_trace"]
                for node in ("ensure_order", "expand_queries", "retrieve_policy"))):
        report["cases"][-1].update(passed=False, error="FAQ must use single retrieval")
    turn("explicit", "订单1001未拆封，能退吗", intent="退款退货", core=True)
    turn("other", "我想给月亮安装一个退款按钮", intent="其他")
    turn("weak", "订单1001能退吗", intent="退款退货", core=True, weak=True,
         filters={"product_category": "不存在的政策类别"})
    waiting = turn("missing", "这个能退吗", intent="退款退货", waiting=True, sse=True)
    if waiting:
        session, selection = waiting["session_id"], waiting["order_selection"]
        request_case(client, report, "pending", "GET", f"/ch06/sessions/{session}/pending?user_id={user}",
                     validator=lambda data: validate_turn(data, waiting=True))
        payload = {"session_id": session, "user_id": user,
                   "selection_id": selection["selection_id"], "order_id": "1001"}
        selected = request_case(client, report, "select", "POST", "/ch06/orders/selection/stream",
                                json=payload, sse=True, validator=lambda data: validate_turn(data,
                                    intent="退款退货", core=True))
        if selected:
            if selected["calls"].get("classifier") != waiting["calls"].get("classifier"):
                report["cases"][-1].update(passed=False, error="selection restarted classification")
            replay = request_case(client, report, "select_replay", "POST", "/ch06/orders/selection",
                                  json=payload, validator=lambda data: validate_turn(data, core=True))
            if replay and replay != selected:
                report["cases"][-1].update(passed=False, error="selection replay changed result")
            offer = selected.get("refund_offer")
            if not offer:
                report["cases"].append({"name": "refund_offer", "passed": False, "error": "offer missing"})
            else:
                form = {"session_id": session, "user_id": user, "offer_id": offer["offer_id"],
                        "order_id": "1001", "reason": "七天无理由", "confirmed": True}
                for name in ("refund_submit", "refund_replay"):
                    request_case(client, report, name, "POST", "/ch06/refunds", json=form,
                                 validator=lambda data: validate_receipt(data, offer))
                request_case(client, report, "receipt", "GET",
                             f"/ch06/refunds/{offer['offer_id']}?session_id={session}&user_id={user}",
                             validator=lambda data: validate_receipt(data, offer))
                request_case(client, report, "changed_reason", "POST", "/ch06/refunds",
                             json={**form, "reason": "其他"}, expected_status=409)
    multi = prefix + "-switch"
    first = turn("logistics_first", "订单1001物流到哪了", session=multi, intent="物流")
    if first:
        second = turn("refund_reference", "这个能退吗", session=multi, intent="退款退货", core=True)
        third = turn("back_logistics", "它的物流到哪了", session=multi, intent="物流")
        for result in (second, third):
            if result and "1001" not in result["resolved_question"]:
                report["cases"].append({"name": "reference_entity", "passed": False, "error": "1001 missing"})
    # Two independently observed orders must remain ambiguous rather than guessed.
    double = prefix + "-double"
    turn("observe_two", "查订单1001和1002的物流", session=double, intent="物流")
    turn("two_candidates", "这个能退吗", session=double, intent="退款退货", waiting=True)
    switch = prefix + "-cancel"
    old = turn("cancel_wait", "我想退一笔订单", session=switch, intent="退款退货", waiting=True)
    if old:
        turn("cancel_with_text", "查订单1001物流", session=switch, intent="物流")
        request_case(client, report, "expired_selection", "POST", "/ch06/orders/selection", expected_status=409,
                     json={"session_id": switch, "user_id": user, "order_id": "1001",
                           "selection_id": old["order_selection"]["selection_id"]})


def serve(args):
    from contextlib import asynccontextmanager

    import uvicorn

    from mewhelp.ch05.config import Ch05Settings
    from mewhelp.ch05.runtime import open_runtime
    from mewhelp.ch06.config import Ch06Settings
    from mewhelp.ch06.policy_evaluation import isolated_policy_runtime
    from mewhelp.config import get_settings
    from mewhelp.knowledge.answering import get_rag_runtime
    from mewhelp.knowledge.api import KbRuntime, get_kb_runtime
    from mewhelp.main import app
    from scripts.migrate_ch06_schema import migrate_ch06

    retrieval = isolated_policy_runtime(args.workdir, args.collection)
    migrate_ch06(retrieval.session_factory.kw["bind"])
    rag = get_rag_runtime(retrieval.session_factory,
                         calibration_path=get_settings().rag_calibration_path, collection=args.collection)
    router = Ch06Settings(calibration_path=args.calibration,
                         policy_calibration_path=args.policy_calibration)

    @asynccontextmanager
    async def lifespan(application):
        async with open_runtime(retrieval.session_factory, rag_factory=lambda: rag,
            settings=Ch05Settings(checkpoint_path=args.workdir / "workflow.sqlite3"),
            router_settings=router) as runtime:
            application.state.ch05_runtime = runtime
            application.dependency_overrides[get_kb_runtime] = lambda: KbRuntime(
                retrieval.session_factory, lambda _: None)
            try:
                yield
            finally:
                del application.state.ch05_runtime
                application.dependency_overrides.clear()

    app.router.lifespan_context = lifespan
    uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--workdir", type=Path)
    parser.add_argument("--port", type=int, default=9006)
    parser.add_argument("--collection")
    parser.add_argument("--calibration", type=Path)
    parser.add_argument("--policy-calibration", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:9006")
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--session-prefix")
    parser.add_argument("--confirm-demo-refunds", action="store_true")
    args = parser.parse_args()
    if args.serve:
        if not all((args.workdir, args.collection, args.calibration, args.policy_calibration)):
            parser.error("serve requires workdir, collection, calibration and policy-calibration")
        serve(args)
        return 0
    if not args.report_dir or not args.session_prefix or not re.fullmatch(r"ch06-[a-z0-9-]{1,30}", args.session_prefix):
        parser.error("use report-dir and a unique ch06- test session-prefix")
    if not args.confirm_demo_refunds:
        parser.error("acceptance writes explicitly confirmed demo refund applications; add --confirm-demo-refunds")
    args.report_dir.mkdir(parents=True, exist_ok=False)
    report = {"base_url": args.base_url, "session_prefix": args.session_prefix,
              "database_scope": "report service configuration separately; HTTP alone cannot prove MySQL",
              "confirmed_demo_refunds": True, "cases": []}
    with httpx.Client(base_url=args.base_url, timeout=240) as client:
        run_acceptance(client, report, args.session_prefix)
    return save_report(report, args.report_dir / "http.json")


if __name__ == "__main__":
    raise SystemExit(main())
