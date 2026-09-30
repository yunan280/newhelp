import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mewhelp.ch02 import service
from mewhelp.db.base import Base
from mewhelp.db.models import Message, MsgRole
from mewhelp.knowledge.answering import AnswerAssessment, RagRuntime
from mewhelp.knowledge.filters import SearchFilters
from mewhelp.knowledge.query import QueryUnderstanding
from mewhelp.knowledge.refusals import LowConfidenceQuestion, PoolCommitError
from mewhelp.knowledge.retrieval import RankedChunk, RetrievalRuntime
from mewhelp.knowledge.store import KnowledgeDraft, put_chunk, snapshot_chunk
from mewhelp.knowledge.vectors import SearchHit
from mewhelp.main import app


@pytest.fixture
def chat_runtime(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = lambda: Session(engine)
    with factory() as session:
        row = put_chunk(
            session,
            KnowledgeDraft(
                "chat:one",
                "参数",
                "HX-210蓝牙版本",
                "蓝牙5.3",
                "手册 / 蓝牙",
                "manual",
                product_category="耳机",
            ),
        )
        row.vectorize_status = "done"
        row.vector_id = str(row.id)
        chunk = snapshot_chunk(row)
        session.commit()
    state = {
        "hits": [SearchHit(chunk.id, chunk.content_hash)],
        "filters": [],
        "generate": 0,
        "error": None,
    }

    class Index:
        def search(self, strategy, **kwargs):
            state["filters"].append(kwargs["filters"])
            if state["error"]:
                raise state["error"]
            return state["hits"]

    async def generate(messages):
        state["generate"] += 1
        state["messages"] = messages
        return state.get(
            "assessment",
            AnswerAssessment(
                answerable=True, reason="充分", answer="蓝牙5.3[1]。", citation_numbers=[1]
            ),
        )

    rt = RagRuntime(
        RetrievalRuntime(
            factory,
            lambda texts: [[0.1] * 1024],
            Index(),
            lambda q, chunks: [RankedChunk(c, 0.9) for c in chunks],
        ),
        generate,
        factory,
        0.5,
        30000,
    )

    async def understand(question, **kwargs):
        return QueryUnderstanding(question, question, question, "knowledge", [])

    monkeypatch.setattr(service, "understand_query", understand, raising=False)
    monkeypatch.setattr(service, "get_rag_runtime", lambda *args, **kwargs: rt, raising=False)
    monkeypatch.setattr(
        service,
        "get_settings",
        lambda: SimpleNamespace(rag_calibration_path=Path("calibration.json")),
        raising=False,
    )

    def no_ordinary_model():
        raise AssertionError("ordinary generation cannot bypass knowledge evidence")

    monkeypatch.setattr(service, "get_chat_model", no_ordinary_model)
    yield factory, rt, state, chunk
    engine.dispose()


@pytest.mark.asyncio
async def test_knowledge_body_waits_for_verified_sources(chat_runtime):
    from mewhelp.ch02.events import SourcesEvent, TokenEvent

    factory, _, state, chunk = chat_runtime
    events = [
        event
        async for event in service.stream_agent_turn(
            factory,
            session_id="ch04",
            user_id="u",
            message="HX-210蓝牙版本？",
            filters=SearchFilters(product_category="耳机"),
        )
    ]
    assert [type(event).__name__ for event in events] == [
        "SessionEvent",
        "ToolEvent",
        "ToolEvent",
        "SourcesEvent",
        "TokenEvent",
        "DoneEvent",
    ]
    sources = next(event for event in events if isinstance(event, SourcesEvent))
    assert sources.sources[0].chunk_id == str(chunk.id) and not sources.refused
    assert "未经校验的前言" not in "".join(
        event.text for event in events if isinstance(event, TokenEvent)
    )
    assert state["filters"][0].product_category == "耳机"
    with factory() as session:
        final = session.scalars(
            select(Message).where(Message.role == MsgRole.assistant).order_by(Message.id.desc())
        ).first()
        assert final.content == "蓝牙5.3[1]。" and final.citations[0]["chunk_id"] == str(chunk.id)


@pytest.mark.asyncio
async def test_refusal_pool_survives_message_ledger_failure(chat_runtime, monkeypatch):
    factory, _, state, _ = chat_runtime
    state["hits"] = []

    def fail(*args, **kwargs):
        raise OSError("message ledger unavailable")

    monkeypatch.setattr(service, "append_messages", fail)
    events = [
        event
        async for event in service.stream_agent_turn(
            factory, session_id="pool", user_id="u", message="HX-999的IP68等级？"
        )
    ]
    sources = next(event for event in events if type(event).__name__ == "SourcesEvent")
    assert sources.refused and events[-1].__class__.__name__ == "DoneEvent"
    with factory() as session:
        rows = session.scalars(select(LowConfidenceQuestion)).all()
        assert len(rows) == 1 and rows[0].original_question == "HX-999的IP68等级？"
        assert rows[0].source_conversation_id is not None and rows[0].entry_point == "chat_stream"


@pytest.mark.asyncio
async def test_invalid_citation_cannot_emit_model_answer(chat_runtime):
    factory, _, state, _ = chat_runtime
    state["assessment"] = AnswerAssessment(
        answerable=True, reason="充分", answer="猜测的答案[99]", citation_numbers=[99]
    )
    events = [
        event
        async for event in service.stream_agent_turn(
            factory, session_id="invalid", user_id="u", message="型号参数？"
        )
    ]
    assert "猜测的答案" not in "".join(getattr(event, "text", "") for event in events)
    assert state["generate"] == 1
    with factory() as session:
        rows = session.scalars(select(LowConfidenceQuestion)).all()
        assert len(rows) == 1 and rows[0].reason_code == "invalid_citation"


@pytest.mark.asyncio
async def test_service_failure_does_not_enter_pool(chat_runtime):
    factory, _, state, _ = chat_runtime
    state["error"] = ConnectionError("Milvus unavailable")
    with pytest.raises(ConnectionError):
        await service.run_agent_turn(factory, session_id="error", user_id="u", message="参数？")
    with factory() as session:
        assert session.scalar(select(LowConfidenceQuestion)) is None


def test_pool_failure_streams_error_without_done(chat_runtime, monkeypatch):
    from mewhelp.ch02 import api
    from mewhelp.knowledge import answering

    factory, _, state, _ = chat_runtime
    state["hits"] = []

    def fail(*args, **kwargs):
        raise PoolCommitError("pool unavailable")

    monkeypatch.setattr(answering, "record_refusal", fail)
    app.dependency_overrides[api.get_session_factory] = lambda: factory
    try:
        response = TestClient(app).post("/ch02/chat/stream", json={"message": "未知问题"})
    finally:
        app.dependency_overrides.clear()
    assert "event: error" in response.text and "event: done" not in response.text
    assert "pool unavailable" in response.text
    assert "event: sources" not in response.text and "event: token" not in response.text


def test_json_sources_and_filters_match_stream_protocol(chat_runtime):
    from mewhelp.ch02 import api

    factory, _, state, chunk = chat_runtime
    app.dependency_overrides[api.get_session_factory] = lambda: factory
    try:
        response = TestClient(app).post(
            "/ch02/agent", json={"message": "HX-210参数", "filters": {"product_category": "耳机"}}
        )
        bad = TestClient(app).post(
            "/ch02/agent", json={"message": "参数", "filters": {"expression": "true"}}
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    assert body["sources"][0]["chunk_id"] == str(chunk.id) and body["refused"] is False
    assert body["low_confidence_question_id"] is None and bad.status_code == 422
    assert state["filters"][0].product_category == "耳机"


@pytest.mark.asyncio
async def test_tool_closure_keeps_original_question_filters_and_full_cached_artifact(chat_runtime):
    from mewhelp.knowledge.answering import QuestionContext
    from mewhelp.tools.knowledge import build_knowledge_tools

    factory, rt, state, chunk = chat_runtime
    long_chunk = replace(chunk, answer="完整证据" * 900, text="完整证据" * 900)

    # The shared core artifact is already verified for this turn; no text truncation in infra.
    def rerank(question, chunks):
        return [RankedChunk(long_chunk, 0.9)]

    rt = replace(rt, retrieval=replace(rt.retrieval, rerank=rerank))
    original = "HX-210蓝牙版本？"
    query = QueryUnderstanding(original, original, original, "knowledge", [])
    tool = build_knowledge_tools(
        factory,
        context=QuestionContext(original, None, "cli"),
        filters=SearchFilters(product_category="耳机"),
        rag_runtime=rt,
        query=query,
    )[0]
    calls = [
        {
            "name": "query_faq",
            "args": {"keyword": "换了问题", "product_category": "手机"},
            "id": str(i),
            "type": "tool_call",
        }
        for i in range(2)
    ]
    results = await asyncio.gather(*(tool.ainvoke(call) for call in calls))
    assert results[0].artifact is results[1].artifact
    assert len(results[0].artifact.final[0].chunk.answer) > 2000
    assert len(state["filters"]) == 1 and state["filters"][0].product_category == "耳机"
    assert state["generate"] == 0


@pytest.mark.asyncio
async def test_business_without_tool_evidence_cannot_release_factual_preamble(
    chat_runtime, monkeypatch
):
    from tests.fakes import FakeToolChatModel, text_chunks

    factory, _, state, _ = chat_runtime

    async def business(question, **kwargs):
        return QueryUnderstanding(question, question, question, "business", [])

    monkeypatch.setattr(service, "understand_query", business)
    model = FakeToolChatModel(rounds=[text_chunks("未经校验的前言：明天到账。")])
    monkeypatch.setattr(service, "get_chat_model", lambda: model)
    state["assessment"] = AnswerAssessment(
        answerable=False, reason="没有该订单证据", answer="", citation_numbers=[]
    )
    events = [
        event
        async for event in service.stream_agent_turn(
            factory,
            session_id="no-business-evidence",
            user_id="u",
            message="订单1001能明天到账吗？",
        )
    ]
    assert "未经校验的前言" not in "".join(getattr(event, "text", "") for event in events)
    assert any(type(event).__name__ == "SourcesEvent" and event.refused for event in events)
    assert type(events[-1]).__name__ == "DoneEvent"


@pytest.mark.asyncio
async def test_multiple_faq_calls_retrieve_once_and_record_original_once(chat_runtime, monkeypatch):
    from mewhelp.tools import knowledge
    from tests.fakes import FakeToolChatModel, tool_call_chunks

    factory, rt, state, _ = chat_runtime
    original = "订单1001涉及的退货政策是什么？"

    async def business(question, **kwargs):
        return QueryUnderstanding(question, question, question, "business", [])

    monkeypatch.setattr(service, "understand_query", business)
    monkeypatch.setattr(knowledge, "get_rag_runtime", lambda *args, **kwargs: rt)
    monkeypatch.setattr(
        knowledge,
        "get_settings",
        lambda: SimpleNamespace(rag_calibration_path=Path("calibration.json")),
    )
    model = FakeToolChatModel(
        rounds=[
            tool_call_chunks("query_faq", '{"keyword":"换掉原话"}', call_id="one", index=0)
            + tool_call_chunks("query_faq", '{"keyword":"再次换掉"}', call_id="two", index=1)
        ]
    )
    monkeypatch.setattr(service, "get_chat_model", lambda: model)
    state["assessment"] = AnswerAssessment(
        answerable=False, reason="规则不完整", answer="", citation_numbers=[]
    )
    out = await service.run_agent_turn(
        factory,
        session_id="multi-faq",
        user_id="u",
        message=original,
        filters=SearchFilters(product_category="耳机"),
    )
    assert out.refused and len(state["filters"]) == 1 and state["generate"] == 1
    assert out.tool_results[0].artifact is out.tool_results[1].artifact
    with factory() as session:
        rows = session.scalars(select(LowConfidenceQuestion)).all()
        assert (
            len(rows) == 1
            and rows[0].original_question == original
            and rows[0].entry_point == "agent"
        )
