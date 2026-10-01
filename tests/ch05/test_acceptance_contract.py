import httpx
import pytest


def checker():
    try:
        from scripts.smoke_ch05_acceptance import check_case
    except ImportError:
        pytest.fail("acceptance validator missing")
    return check_case


def test_same_round_tools_cannot_pass_sequential_acceptance():
    check = checker()
    payload = {
        "tool_trace": [
            {"name": "query_order", "round": 1, "ok": True},
            {"name": "query_logistics", "round": 1, "ok": True},
        ]
    }
    assert check(payload, {"sequential_tools": ["query_order", "query_logistics"]})
    payload["tool_trace"][1]["round"] = 2
    assert check(payload, {"sequential_tools": ["query_order", "query_logistics"]}) == []


def test_acceptance_uses_actual_graph_node_contract():
    from mewhelp.ch05.workflow import build_workflow
    from scripts.smoke_ch05_acceptance import CASES

    nodes = build_workflow(None).get_graph().nodes
    for case in CASES:
        expected = case["expected"]
        assert set(expected.get("nodes", []) + expected.get("forbidden_nodes", [])) <= nodes.keys()


def test_missing_trace_bad_route_and_agent_after_weak_gate_fail():
    check = checker()
    assert check({"node_trace": []}, {"nodes": ["retrieve_knowledge", "confidence_gate"]})
    assert check({"route": "business"}, {"route": "knowledge"})
    assert check({"calls": {"decision": 1, "answer": 1}}, {"agent_calls": 0})
    assert check({"calls": {"decision": 0, "answer": 0}}, {"agent_calls": 0}) == []


def test_handoff_cannot_pass_if_ticket_count_increases():
    check = checker()
    assert check({"tickets_before": 1, "tickets_after": 2}, {"ticket_delta": 0})
    assert check({"tickets_before": 1, "tickets_after": 1}, {"ticket_delta": 0}) == []


def test_service_errors_are_saved_and_return_nonzero(tmp_path, session_factory, monkeypatch):
    try:
        from scripts import smoke_ch05_acceptance as acceptance
    except ImportError:
        pytest.fail("HTTP acceptance runner missing")
    client = httpx.Client(
        base_url="http://test",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(502, json={"detail": "provider unavailable"})
        ),
    )
    monkeypatch.setattr(acceptance.httpx, "Client", lambda **kwargs: client)
    assert (
        acceptance.run_acceptance(
            "http://test",
            report_dir=tmp_path / "report",
            session_prefix="test-fail",
            session_factory=session_factory,
        )
        == 1
    )
    import json

    report = json.loads((tmp_path / "report" / "acceptance.json").read_text())
    assert report["service_errors"] > 0 and report["passed"] == 0


def test_sse_error_or_missing_done_never_becomes_success():
    try:
        from scripts.smoke_ch05_acceptance import completed_sse
    except ImportError:
        pytest.fail("SSE completion validator missing")
    with pytest.raises(ValueError):
        completed_sse('event: error\ndata: {"code":"workflow_error"}\n\n')
    with pytest.raises(ValueError):
        completed_sse('event: token\ndata: {"text":"partial"}\n\n')
