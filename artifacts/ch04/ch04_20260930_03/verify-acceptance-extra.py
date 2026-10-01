"""Labeled live HTTP cases for negative policy and generation insufficiency."""

import datetime as dt
import json
import re
from pathlib import Path

import httpx

from scripts.smoke_ch04_acceptance import _verify_answer, _verify_refusal, sqlite_factory

workdir = Path("artifacts/ch04/acceptance_ch04_20260930_03")
inputs = [
    {
        "id": "negative_policy",
        "question": "退款政策是否承诺具体到账时间？",
        "filters": {},
        "expected": "evidence-backed negative answer",
        "basis": "冻结语料的退款条款与隔离跳转文档明确不承诺具体到账时间。",
    },
    {
        "id": "missing_launch_date",
        "question": "请确认 HX-210S 支持蓝牙5.3，并告诉我 HX-210S 的首发日期。",
        "filters": {"product_category": "耳机"},
        "expected": "refusal",
        "basis": "冻结原文有 HX-210S 蓝牙5.3，没有该型号的首发日期；不能完整回答。",
    },
    {
        "id": "missing_pps_range",
        "question": "CX-65 的 USB-C 输出功率是多少？PPS 电压范围是什么？",
        "filters": {"product_category": "充电器"},
        "expected": "refusal",
        "basis": "冻结原文有 USB-C 输出功率，无 PPS 输出电压范围；不能完整回答。",
    },
    {
        "id": "missing_processor",
        "question": "RX-300S 的无线标准是什么？它的处理器具体型号是什么？",
        "filters": {"product_category": "路由器"},
        "expected": "refusal",
        "basis": "冻结原文有无线标准，无处理器型号；不能完整回答。",
    },
    {
        "id": "missing_panel_supplier",
        "question": "MX-24P 的屏幕分辨率是什么？面板供应商是哪家？",
        "filters": {"product_category": "显示器"},
        "expected": "refusal",
        "basis": "冻结原文有分辨率，无面板供应商；不能完整回答。",
    },
]


def freeze():
    out = Path("artifacts/ch04/ch04_20260930_03/acceptance-extra-inputs.json")
    if out.exists():
        assert json.loads(out.read_text(encoding="utf-8")) == inputs
    else:
        out.write_text(json.dumps(inputs, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    freeze()
    factory = sqlite_factory(workdir / "acceptance.sqlite")
    started = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    report = {"scope": "isolated_example_live_http", "records": [], "passed": False}
    try:
        with httpx.Client(base_url="http://127.0.0.1:8001", timeout=300) as client:
            for case in inputs:
                response = client.post(
                    "/ch02/agent",
                    json={
                        "user_id": "ch04-extra-smoke",
                        "message": case["question"],
                        "filters": case["filters"],
                    },
                )
                response.raise_for_status()
                payload = response.json()
                record = {"case": case, "payload": payload, "pool": None}
                report["records"].append(record)
                if case["expected"] == "refusal":
                    record["pool"] = _verify_refusal(
                        payload, factory, case["question"], "agent", started
                    )
                else:
                    _verify_answer(payload, client)
                    assert re.search(
                        r"不承诺|不能承诺|无法承诺|不保证|不能保证|无法保证", payload["answer"]
                    ), "negative policy not stated"
                print(
                    case["id"], "pool_stage=" + str((record["pool"] or {}).get("stage")), flush=True
                )
        generation = [
            r
            for r in report["records"]
            if (r["pool"] or {}).get("stage") == "generation"
            and r["pool"]["reason_code"] == "insufficient_evidence"
        ]
        assert generation, (
            "no live generation-stage insufficiency pool row; retrieval-only refusal is not enough"
        )
        report["generation_insufficiency_cases"] = [r["case"]["id"] for r in generation]
        report["passed"] = True
    except Exception as exc:  # noqa: BLE001 — save failed acceptance and exit nonzero
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["completed_at_utc"] = dt.datetime.now(dt.UTC).isoformat()
    out = Path("artifacts/ch04/ch04_20260930_03/acceptance-extra.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps({"passed": report["passed"], "error": report.get("error")}, ensure_ascii=False),
        flush=True,
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    import sys

    if "--freeze" in sys.argv:
        freeze()
    else:
        raise SystemExit(main())
