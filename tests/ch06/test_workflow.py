from dataclasses import replace

from langchain_core.messages import AIMessage
from sqlalchemy import select

from mewhelp.ch05.schemas import OrderResumeRequest, TurnRequest
from mewhelp.ch05.service import run_turn, stream_turn
from mewhelp.db.models import Message, MsgRole


async def test_order_policy_sequence_and_source_before_first_token(core_runtime):
    events = [e async for e in stream_turn(core_runtime, TurnRequest(message="订单1001能退吗", session_id="core"), entry_point="chat_stream")]
    result = events[-1]["data"]
    trace = result["node_trace"]
    required = ["understand_query", "classify_intent", "load_order", "expand_queries", "retrieve_policy", "confidence_gate", "assess_order", "stream_answer"]
    assert [trace.index(n) for n in required] == sorted(trace.index(n) for n in required)
    assert result["order"]["order_id"] == "1001" and result["assessment"]["verdict"] == "eligible"
    names = [e["event"] for e in events]
    assert names.index("sources") < names.index("token") and "agent_decide" not in trace


async def test_wait_resume_writes_original_user_once(core_runtime, core_factory, session_factory):
    from mewhelp.ch06.selection import resume_order
    core_factory.understanding_scope = "order_specific"
    before = await run_turn(core_runtime, TurnRequest(message="这个能退吗", session_id="missing"))
    assert before.status == "waiting_for_order" and before.calls.get("expansion", 0) == 0
    after = await resume_order(core_runtime, OrderResumeRequest(session_id="missing",
        selection_id=before.order_selection.selection_id, order_id="1001"))
    assert after.assessment.verdict == "eligible" and after.calls["classifier"] == before.calls["classifier"]
    await resume_order(core_runtime, OrderResumeRequest(session_id="missing",
        selection_id=before.order_selection.selection_id, order_id="1001"))
    with session_factory() as db:
        rows = db.scalars(select(Message).where(Message.conversation_id == before.conversation_id)).all()
        assert len([r for r in rows if r.role == MsgRole.user and r.content == "这个能退吗"]) == 1
        assert len([r for r in rows if r.role == MsgRole.assistant]) == 2


async def test_faq_skips_order_and_expansion(core_runtime):
    result = await run_turn(core_runtime, TurnRequest(message="一般退款政策是什么"))
    assert result.route == "knowledge" and "ensure_order" not in result.node_trace
    assert result.calls.get("expansion", 0) == 0 and result.node_trace.count("retrieve_knowledge") == 1


async def test_weak_policy_never_calls_assessment(core_runtime):
    core_runtime.test_evidence.scores = [.01]
    result = await run_turn(core_runtime, TurnRequest(message="订单1001能退吗"))
    assert result.route == "aftersales" and result.refused and result.assessment is None and result.calls.get("assessment", 0) == 0


async def test_unknown_condition_requires_agent_clarification(core_runtime, core_factory):
    core_factory.assessment_verdict = "needs_clarification"
    result = await run_turn(core_runtime, TurnRequest(message="订单1002能退吗"))
    assert result.order.condition == "unknown" and result.assessment.verdict == "needs_clarification"
    assert result.refund_offer is None and result.stop_reason == "clarification"


async def test_logistics_refund_logistics_resolves_and_resets(core_runtime, core_factory):
    core_factory.intents = ["物流", "退款退货", "物流"]
    core_factory.decisions = [AIMessage(content="", tool_calls=[{"name":"query_logistics", "args":{"order_id":"1001"}, "id":"l1", "type":"tool_call"}])]
    await run_turn(core_runtime, TurnRequest(message="订单1001物流在哪", session_id="switch"))
    core_factory.understanding_outputs = [{"question":"订单1001的机械键盘能退货吗", "scope":"order_specific",
        "reference_order_id":"1001", "reference_message_id":None}]
    state = await core_runtime.graph.aget_state({"configurable":{"thread_id":"switch"}})
    core_factory.understanding_outputs[0]["reference_message_id"] = state.values["trusted_entities"][-1]["message_id"]
    refund = await run_turn(core_runtime, TurnRequest(message="它能退吗", session_id="switch"))
    assert "1001" in refund.resolved_question and refund.order.order_id == "1001"
    result = await run_turn(core_runtime, TurnRequest(message="不退了，查订单1001物流", session_id="switch"))
    assert result.intent == "物流" and result.order is None and result.sources == [] and result.assessment is None


async def test_understanding_budget_stops_without_provider_request(core_runtime, core_factory):
    core_runtime.context.limits = replace(core_runtime.context.limits, total_model_tokens=500)
    result = await run_turn(core_runtime, TurnRequest(message="这个能退吗"))
    assert result.stop_reason == "token_budget" and core_factory.requests == []


async def test_sql_commit_then_checkpoint_failure_is_retryable(core_runtime, core_factory, session_factory, monkeypatch):
    import pytest

    from mewhelp.ch06.selection import resume_order
    core_factory.understanding_scope = "order_specific"
    before = await run_turn(core_runtime, TurnRequest(message="这个能退吗", session_id="fault"))
    saver = core_runtime.graph.checkpointer
    original = saver.aput
    failures = []
    async def interrupted(config, checkpoint, metadata, new_versions):
        values = checkpoint.get("channel_values", {})
        if values.get("node_trace", [])[-1:] == ["log_turn"] and not failures:
            failures.append(True)
            raise OSError("checkpoint unavailable after SQL commit")
        return await original(config, checkpoint, metadata, new_versions)
    monkeypatch.setattr(saver, "aput", interrupted)
    chosen = OrderResumeRequest(session_id="fault", selection_id=before.order_selection.selection_id, order_id="1001")
    with pytest.raises(OSError, match="checkpoint"):
        await resume_order(core_runtime, chosen)
    after = await resume_order(core_runtime, chosen)
    assert after.order.order_id == "1001" and after.ledger_error is None
    with session_factory() as db:
        rows = db.scalars(select(Message).where(Message.conversation_id == after.conversation_id)).all()
        assert len([r for r in rows if r.role == MsgRole.user]) == 1
        assert len([r for r in rows if r.role == MsgRole.assistant]) == 2


async def test_observed_usage_exhaustion_stops_before_classifier(core_runtime):
    import json
    class LargeUsage:
        async def ainvoke(self, messages):
            question = json.loads(messages[-1].content)["question"]
            return AIMessage(content=json.dumps({"question":question, "scope":"order_specific",
                "reference_order_id":None, "reference_message_id":None}),
                usage_metadata={"input_tokens":64000, "output_tokens":100, "total_tokens":64100})
    core_runtime.context.router_model_factory = lambda **kw: LargeUsage()
    result = await run_turn(core_runtime, TurnRequest(message="这个能退吗"))
    assert result.stop_reason == "token_budget" and result.calls["classifier"] == 0
    assert result.usage.total == 64100
