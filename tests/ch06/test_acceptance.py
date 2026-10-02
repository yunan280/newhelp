import copy
import hashlib
import importlib
import json

import httpx
import pytest


def acceptance():
    return importlib.import_module("scripts.smoke_ch06_acceptance")


def turn():
    return {"session_id": "ch06-test", "conversation_id": 1, "resumed": False,
            "answer": "请选择订单", "intent": "退款退货", "route": "aftersales",
            "node_trace": ["understand_query", "classify_intent", "ensure_order",
                           "load_order", "expand_queries", "retrieve_policy",
                           "confidence_gate", "assess_order", "stream_answer", "log_turn"],
            "usage": {"input_tokens": 1, "output_tokens": 1, "estimated": False},
            "calls": {}, "stop_reason": "completed"}


def test_http_502_is_saved_and_counts_as_failed_case(tmp_path):
    module = acceptance()
    report = {"cases": []}
    with httpx.Client(base_url="http://test", transport=httpx.MockTransport(
        lambda _: httpx.Response(502, json={"detail": "provider failed"})
    )) as client:
        assert module.request_case(client, report, "real_error", "POST", "/ch05/agent",
                                   json={}, validator=lambda data: data) is None
    assert module.save_report(report, tmp_path / "http.json") == 1
    saved = json.loads((tmp_path / "http.json").read_text())
    assert saved["total"] == 1 and saved["passed"] == 0 and saved["service_errors"] == 1
    assert saved["cases"][0]["response"]["detail"] == "provider failed"


def test_selector_frame_without_waiting_terminal_is_failure():
    with pytest.raises(ValueError, match="terminal"):
        acceptance().sse_result('event: order_selection\ndata: {"selection_id":"x"}\n\n')


def test_sse_error_is_failure_even_with_done_frame():
    with pytest.raises(ValueError, match="error"):
        acceptance().sse_result('event: error\ndata: {"message":"failed"}\n\n'
                                + 'event: done\ndata: ' + json.dumps(turn()) + '\n\n')


def test_wrong_core_node_sequence_is_failure():
    wrong = copy.deepcopy(turn())
    wrong["node_trace"][3:5] = ["expand_queries", "load_order"]
    with pytest.raises(ValueError, match="sequence"):
        acceptance().validate_turn(wrong, intent="退款退货", core=True)


def test_fake_application_number_is_failure_and_stable_receipt_passes():
    module = acceptance()
    offer = {"offer_id": "a" * 64, "order": {"order_id": "1001"}}
    receipt = {"application_no": "RFfake", "order_id": "1001", "reason": "七天无理由",
               "status": "pending", "replayed": False}
    with pytest.raises(ValueError, match="application"):
        module.validate_receipt(receipt, offer)
    receipt["application_no"] = "RF" + hashlib.sha256(offer["offer_id"].encode()).hexdigest()[:30]
    assert module.validate_receipt(receipt, offer) == receipt


def test_waiting_result_cannot_be_treated_as_completed():
    wrong = turn()
    wrong["status"] = "waiting_for_order"
    with pytest.raises(ValueError, match="waiting"):
        acceptance().validate_turn(wrong, intent="退款退货", core=True)
