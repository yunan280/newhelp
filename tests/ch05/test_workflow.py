import asyncio

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from sqlalchemy import select

from mewhelp.ch05.schemas import TurnRequest
from mewhelp.db.models import Message, MsgRole, Ticket
from mewhelp.knowledge.refusals import LowConfidenceQuestion


def service():
    from mewhelp.ch05 import service

    return service


async def test_plaintext_decision_completes_safely_and_next_turn_can_continue(
    workflow_runtime, model_factory, session_factory
):
    model_factory.decisions = [AIMessage(content="邮费规则如下：满99元包邮。") for _ in range(2)]
    request = TurnRequest(message="邮费是多少", session_id="invalid-control")
    events = [
        e async for e in service().stream_turn(workflow_runtime, request, entry_point="chat_stream")
    ]
    done = events[-1]["data"]
    assert events[-1]["event"] == "done" and done["stop_reason"] == "invalid_decision"
    assert done["calls"] == {"classifier": 1, "decision": 2, "answer": 0}
    assert "stream_answer" not in done["node_trace"] and done["node_trace"][-1] == "log_turn"
    assert "".join(e["data"]["text"] for e in events if e["event"] == "token") == done["answer"]
    saved = await workflow_runtime.graph.aget_state(
        {"configurable": {"thread_id": "invalid-control"}}
    )
    assert saved.values["messages"][-1].content == done["answer"]
    with session_factory() as db:
        assert db.scalars(select(Ticket)).all() == []
        answers = db.scalars(select(Message).where(Message.role == MsgRole.assistant)).all()
        assert answers[-1].content == done["answer"]
    next_turn = await service().run_turn(
        workflow_runtime, TurnRequest(message="你好", session_id="invalid-control")
    )
    assert next_turn.stop_reason == "completed" and next_turn.actions == []


async def test_truncated_answer_stream_checkpoint_and_ledger_agree(
    workflow_runtime, model_factory, session_factory
):
    model_factory.intents = ["订单"]
    original = workflow_runtime.context.model_factory

    class TruncatedModel:
        async def astream(self, messages):
            yield AIMessageChunk(content="退款条件包括以下三项：第一，")
            yield AIMessageChunk(
                content="",
                response_metadata={"finish_reason": "length"},
                usage_metadata={"input_tokens": 100, "output_tokens": 1024, "total_tokens": 1124},
            )

    workflow_runtime.context.model_factory = lambda *a, **kw: (
        TruncatedModel() if kw.get("streaming") else original(*a, **kw)
    )
    request = TurnRequest(message="说明订单信息", session_id="truncated")
    events = [
        e async for e in service().stream_turn(workflow_runtime, request, entry_point="chat_stream")
    ]
    done = events[-1]["data"]
    assert done["stop_reason"] == "output_limit"
    assert "不完整" in done["answer"] and "限额" in done["answer"]
    assert done["actions"] == ["handoff"]
    assert "".join(e["data"]["text"] for e in events if e["event"] == "token") == done["answer"]
    saved = await workflow_runtime.graph.aget_state({"configurable": {"thread_id": "truncated"}})
    assert saved.values["messages"][-1].content == done["answer"]
    with session_factory() as db:
        answers = db.scalars(select(Message).where(Message.role == MsgRole.assistant)).all()
        assert answers[-1].content == done["answer"]


async def test_reranker_oversize_goes_through_gate_and_commits_before_token(
    workflow_runtime, knowledge_request, session_factory, monkeypatch
):
    from types import SimpleNamespace

    from mewhelp.ch05 import evidence, workflow
    from mewhelp.knowledge.reranking import UnsupportedContextError

    def oversized(*args):
        raise UnsupportedContextError("reranker pair exceeds 8192 tokens")

    monkeypatch.setattr(evidence, "retrieve_evidence", oversized)
    monkeypatch.setattr(workflow, "retrieve_knowledge", evidence.retrieve_knowledge)
    workflow_runtime.context.rag_factory = lambda: SimpleNamespace(
        retrieval=object(), relevance_threshold=0.5, context_budget=32000
    )
    events = []
    async for item in service().stream_turn(
        workflow_runtime, knowledge_request, entry_point="chat_stream"
    ):
        events.append(item)
        if item["event"] == "token":
            with session_factory() as db:
                row = db.scalar(select(LowConfidenceQuestion))
                assert row.reason_code == "unsupported_context_size"
                assert row.original_question == knowledge_request.message
                assert row.trigger_stage == "retrieval"
    done = events[-1]["data"]
    assert done["refused"] and done["calls"]["decision"] == done["calls"]["answer"] == 0
    assert done["node_trace"].index("confidence_gate") < done["node_trace"].index("fallback_reply")
    assert "agent_decide" not in done["node_trace"]


@pytest.mark.parametrize("intent", ["商品咨询", "退款退货"])
async def test_workflow_gates_knowledge_before_agent(
    workflow_runtime, knowledge_request, model_factory, intent
):
    model_factory.intents = [intent]
    result = await service().run_turn(workflow_runtime, knowledge_request)
    order = result.node_trace
    assert order.index("retrieve_knowledge") < order.index("confidence_gate")
    assert order.index("confidence_gate") < order.index("agent_decide")
    assert order[-1] == "log_turn" and not result.refused


async def test_weak_knowledge_records_before_fallback_without_agent(
    workflow_runtime, knowledge_request, strong_evidence, session_factory
):
    strong_evidence.scores = [0.49]
    events = [
        e
        async for e in service().stream_turn(
            workflow_runtime, knowledge_request, entry_point="chat_stream"
        )
    ]
    done = events[-1]["data"]
    assert done["refused"] and done["calls"]["decision"] == done["calls"]["answer"] == 0
    assert "agent_decide" not in done["node_trace"]
    with session_factory() as db:
        row = db.scalar(select(LowConfidenceQuestion))
        assert str(row.id) == done["low_confidence_question_id"]


@pytest.mark.parametrize("intent", ["物流", "订单", "售后"])
async def test_business_skips_retrieval_and_gate(workflow_runtime, model_factory, intent):
    model_factory.intents = [intent]
    result = await service().run_turn(workflow_runtime, TurnRequest(message="查询业务"))
    assert result.route == "business"
    assert (
        "retrieve_knowledge" not in result.node_trace and "confidence_gate" not in result.node_trace
    )


async def test_complaint_and_greeting_cannot_write_ticket(
    workflow_runtime, model_factory, session_factory
):
    model_factory.intents = ["投诉"]
    first = await service().run_turn(
        workflow_runtime, TurnRequest(message="我要投诉", session_id="x")
    )
    assert first.actions == ["handoff", "create_ticket"] and first.offer
    assert first.calls == {"classifier": 1, "decision": 0, "answer": 0}
    second = await service().run_turn(workflow_runtime, TurnRequest(message="你好", session_id="x"))
    assert second.calls == {"classifier": 0, "decision": 0, "answer": 0}
    assert second.actions == [] and second.offer is None and second.sources == []
    state = await workflow_runtime.graph.aget_state({"configurable": {"thread_id": "x"}})
    assert first.offer.offer_id in state.values["offers"]
    with session_factory() as db:
        assert db.scalars(select(Ticket)).all() == []


async def test_classified_chitchat_uses_only_classifier(workflow_runtime, model_factory):
    model_factory.intents = ["闲聊"]
    result = await service().run_turn(workflow_runtime, TurnRequest(message="天气真好"))
    assert result.calls == {"classifier": 1, "decision": 0, "answer": 0}


async def test_sources_precede_first_token_and_next_turn_resets_evidence(
    workflow_runtime, knowledge_request, model_factory
):
    events = [
        e
        async for e in service().stream_turn(
            workflow_runtime, knowledge_request, entry_point="agent"
        )
    ]
    names = [e["event"] for e in events]
    assert names[0] == "session" and names.index("sources") < names.index("token")
    model_factory.intents = ["物流"]
    result = await service().run_turn(
        workflow_runtime,
        TurnRequest(message="订单1001物流", session_id=knowledge_request.session_id),
    )
    assert result.sources == [] and not result.refused
    decision_request = [m for kind, m in model_factory.requests if kind == "decision"][-1]
    assert not any("七天无理由" in str(m.content) for m in decision_request if m.type == "system")


async def test_tool_ledger_pairs_calls_and_results(
    workflow_runtime, model_factory, session_factory
):
    model_factory.intents = ["订单"]
    model_factory.decisions = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "query_order",
                    "args": {"order_id": "1001"},
                    "id": "order-1",
                    "type": "tool_call",
                }
            ],
        )
    ]
    result = await service().run_turn(workflow_runtime, TurnRequest(message="订单1001"))
    with session_factory() as db:
        rows = db.scalars(
            select(Message)
            .where(Message.conversation_id == result.conversation_id)
            .order_by(Message.id)
        ).all()
        assert [r.role for r in rows] == [
            MsgRole.user,
            MsgRole.assistant,
            MsgRole.tool,
            MsgRole.assistant,
        ]
        assert rows[1].tool_calls[0]["id"] == rows[2].tool_call_id == "order-1"


async def test_concurrent_same_session_keeps_both_completed_turns(workflow_runtime):
    await asyncio.gather(
        *(
            service().run_turn(workflow_runtime, TurnRequest(message="你好", session_id="serial"))
            for _ in range(2)
        )
    )
    snapshot = await workflow_runtime.graph.aget_state({"configurable": {"thread_id": "serial"}})
    assert len(snapshot.values["messages"]) == 4


async def test_json_run_releases_session_lock_before_return(workflow_runtime):
    result = await service().run_turn(workflow_runtime, TurnRequest(message="你好"))
    assert not workflow_runtime.locks.lock(result.session_id).locked()


async def test_ledger_failure_is_visible_and_checkpoint_still_completes(
    workflow_runtime, monkeypatch
):
    from mewhelp.ch05 import workflow

    def broken(*args, **kwargs):
        raise RuntimeError("ledger failed")

    monkeypatch.setattr(workflow, "append_messages", broken)
    result = await service().run_turn(workflow_runtime, TurnRequest(message="你好"))
    assert result.ledger_error
    snapshot = await workflow_runtime.graph.aget_state(
        {"configurable": {"thread_id": result.session_id}}
    )
    assert len(snapshot.values["messages"]) == 2
