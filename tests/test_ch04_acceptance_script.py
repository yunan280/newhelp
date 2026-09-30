import datetime as dt
import importlib.util
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mewhelp.db.base import Base
from mewhelp.db.models import Conversation
from mewhelp.knowledge.answering import source_dtos
from mewhelp.knowledge.refusals import LowConfidenceQuestion, RefusalInput, record_refusal
from mewhelp.knowledge.retrieval import RankedChunk, RetrievalResult
from mewhelp.knowledge.store import KnowledgeDraft, put_chunk, snapshot_chunk


@pytest.fixture
def smoke():
    path = Path(__file__).resolve().parents[1] / "scripts" / "smoke_ch04_acceptance.py"
    spec = importlib.util.spec_from_file_location("smoke_ch04", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def ledger():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = lambda: Session(engine)
    with factory() as session:
        session.add_all(
            [
                Conversation(id=1, session_id="acceptance-session", user_id="qa"),
                Conversation(id=2, session_id="wrong-session", user_id="qa"),
            ]
        )
        row = put_chunk(
            session,
            KnowledgeDraft(
                "smoke:one", "物流", "运费怎么计算", "满99元免运费。", "FAQ / 运费", "faq"
            ),
        )
        row.vector_id, row.vectorize_status = str(row.id), "done"
        snapshot = snapshot_chunk(row)
        session.commit()
    yield factory, snapshot
    engine.dispose()


def test_acceptance_assertions_can_pass_for_consistent_http_and_ledger(
    smoke, ledger, tmp_path, monkeypatch
):
    factory, snapshot = ledger
    install_client(smoke, factory, snapshot, monkeypatch)
    assert smoke.main("http://fixture", report_dir=tmp_path, session_factory=factory) == 0


def install_client(smoke, factory, snapshot, monkeypatch, damage=None):
    source = source_dtos(RetrievalResult([snapshot], [RankedChunk(snapshot, 0.9)]))[0].model_dump()

    def handler(request):
        if request.method == "GET":
            return httpx.Response(
                200, json={key: value for key, value in source.items() if key != "number"}
            )
        body = json.loads(request.content)
        question = body["message"]
        refused = question != snapshot.questions
        payload = {
            "session_id": "acceptance-session",
            "conversation_id": 1,
            "answer": "依据不足，无法确认。" if refused else "满99元免运费[1]。",
            "refused": refused,
            "sources": [] if refused else [source.copy()],
            "low_confidence_question_id": None,
        }
        if refused and damage != "missing_pool":
            entry = "agent" if request.url.path == "/ch02/agent" else "chat_stream"
            if damage in ("wrong_conversation", "empty_reason"):
                with factory() as session:
                    row = LowConfidenceQuestion(
                        original_question=question,
                        source_conversation_id=2 if damage == "wrong_conversation" else 1,
                        entry_point=entry,
                        trigger_stage="retrieval",
                        reason_code="no_evidence",
                        reason=" " if damage == "empty_reason" else "无匹配原文",
                    )
                    session.add(row)
                    session.flush()
                    payload["low_confidence_question_id"] = str(row.id)
                    session.commit()
            else:
                payload["low_confidence_question_id"] = record_refusal(
                    factory,
                    RefusalInput(
                        question,
                        1,
                        entry,
                        "retrieval",
                        "no_evidence",
                        "无匹配原文",
                    ),
                )
        if not refused and damage == "missing_sources":
            payload["sources"] = []
        if not refused and damage == "numeric_id":
            payload["sources"][0]["chunk_id"] = int(source["chunk_id"])
        if not refused and damage == "unmapped_citation":
            payload["answer"] = "满99元免运费[99]。"
        if not refused and damage == "wrong_original":
            payload["sources"][0]["answer"] = "包邮门槛改了。"
        if request.url.path == "/ch02/chat/stream":
            frames = [
                ("session", {"session_id": payload["session_id"], "resumed": True}),
                (
                    "sources",
                    {
                        key: payload[key]
                        for key in ("sources", "refused", "low_confidence_question_id")
                    },
                ),
                ("token", {"text": payload["answer"]}),
                ("done", {"finish_reason": "stop"}),
            ]
            if damage == "sources_after_token":
                frames[1], frames[2] = frames[2], frames[1]
            return httpx.Response(
                200,
                text="".join(
                    f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in frames
                ),
            )
        return httpx.Response(200, json=payload)

    real_client = httpx.Client
    monkeypatch.setattr(
        smoke.httpx,
        "Client",
        lambda **kwargs: real_client(**kwargs, transport=httpx.MockTransport(handler)),
    )


@pytest.mark.parametrize(
    "damage",
    [
        "missing_sources",
        "numeric_id",
        "missing_pool",
        "wrong_conversation",
        "empty_reason",
        "unmapped_citation",
        "wrong_original",
        "sources_after_token",
    ],
)
def test_false_success_is_rejected(smoke, ledger, tmp_path, monkeypatch, damage):
    factory, snapshot = ledger
    install_client(smoke, factory, snapshot, monkeypatch, damage)
    assert smoke.main("http://fixture", report_dir=tmp_path, session_factory=factory) != 0


@pytest.mark.parametrize("offset,accepted", [(0.2, True), (3, False), (-30, False)])
def test_pool_timestamp_allows_only_second_precision_rounding(
    smoke, ledger, monkeypatch, offset, accepted
):
    factory, _snapshot = ledger
    wall_clock = dt.datetime(2026, 9, 30, 13, 2, 32, 800000, tzinfo=dt.UTC).replace(
        tzinfo=None
    )
    with factory() as session:
        row = LowConfidenceQuestion(
            original_question=smoke.UNKNOWN_QUESTION,
            source_conversation_id=1,
            entry_point="agent",
            trigger_stage="retrieval",
            reason_code="no_evidence",
            reason="无匹配原文",
            created_at=wall_clock + dt.timedelta(seconds=offset),
        )
        session.add(row)
        session.flush()
        pool_id = str(row.id)
        session.commit()

    class FixedDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return wall_clock.replace(tzinfo=tz)

    monkeypatch.setattr(smoke.dt, "datetime", FixedDatetime)
    payload = {
        "refused": True,
        "sources": [],
        "answer": "依据不足，无法确认。",
        "conversation_id": 1,
        "low_confidence_question_id": pool_id,
    }
    args = (payload, factory, smoke.UNKNOWN_QUESTION, "agent", wall_clock)
    if accepted:
        assert smoke._verify_refusal(*args)["id"] == pool_id
    else:
        with pytest.raises(AssertionError, match="timestamp"):
            smoke._verify_refusal(*args)
