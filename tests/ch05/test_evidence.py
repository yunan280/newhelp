from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from mewhelp.db.base import Base
from mewhelp.db.models import Conversation
from mewhelp.knowledge.answering import SourceDTO
from mewhelp.knowledge.filters import SearchFilters
from mewhelp.knowledge.refusals import LowConfidenceQuestion, PoolCommitError


def module():
    try:
        from mewhelp.ch05 import evidence
    except ImportError:
        pytest.fail("pre-Agent evidence gate missing")
    return evidence


def envelope(**updates):
    source = SourceDTO(
        number=1,
        chunk_id="1",
        questions="退货",
        answer="七天无理由",
        section_path="退货政策",
        category="政策",
        product_category=None,
        content_hash="a" * 64,
        source_url="/kb/source/1",
    )
    return module().EvidenceEnvelope(
        **(
            {
                "sources": [source],
                "scores": [0.49],
                "threshold": 0.5,
                "context_budget": 32000,
                "unsupported_reason": None,
            }
            | updates
        )
    )


@pytest.mark.parametrize("score,passed", [(0.5, True), (0.49, False)])
def test_gate_uses_calibrated_boundary(score, passed):
    result = module().evaluate_gate(envelope(scores=[score]), prompt_bytes=300)
    assert result.passed is passed
    assert result.reason_code == (None if passed else "low_relevance")


def test_empty_and_oversized_evidence_are_refusals():
    m = module()
    assert (
        m.evaluate_gate(envelope(sources=[], scores=[]), prompt_bytes=300).reason_code
        == "no_evidence"
    )
    assert (
        m.evaluate_gate(envelope(scores=[0.9]), prompt_bytes=32001).reason_code
        == "unsupported_context_size"
    )


@pytest.mark.parametrize(
    "updates",
    [{"scores": [None]}, {"scores": [float("nan")]}, {"threshold": float("nan")}, {"scores": []}],
)
def test_bad_scores_are_configuration_errors(updates):
    with pytest.raises(ValueError):
        module().evaluate_gate(envelope(**updates), prompt_bytes=300)


async def test_refusal_commits_original_question_separately():
    m = module()
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        conv = Conversation(session_id="gate-test", user_id="demo-user")
        session.add(conv)
        session.commit()
        cid = conv.id
    gate = m.evaluate_gate(envelope(), prompt_bytes=300)
    row_id = await m.persist_refusal(
        SimpleNamespace(session_factory=factory),
        {"question": " 原问题不能改写 ", "conversation_id": cid, "entry_point": "agent"},
        gate,
    )
    with factory() as session:
        row = session.scalar(select(LowConfidenceQuestion))
        assert str(row.id) == row_id
        assert row.original_question == " 原问题不能改写 "
        assert row.trigger_stage == "retrieval"
    engine.dispose()


async def test_pool_failure_is_not_claimed_as_saved():
    def broken():
        raise RuntimeError("db unavailable")

    m = module()
    with pytest.raises(PoolCommitError):
        await m.persist_refusal(
            SimpleNamespace(session_factory=broken),
            {"question": "政策", "conversation_id": 1, "entry_point": "agent"},
            m.evaluate_gate(envelope(), prompt_bytes=300),
        )


async def test_retrieval_service_failure_is_not_low_confidence(monkeypatch):
    m = module()

    def broken(runtime, query, filters):
        assert query.original == query.canonical == "退货政策"
        raise RuntimeError("Milvus unavailable")

    monkeypatch.setattr(m, "retrieve_evidence", broken)
    rag = SimpleNamespace(retrieval=object(), relevance_threshold=0.5, context_budget=32000)
    with pytest.raises(RuntimeError, match="Milvus"):
        await m.retrieve_knowledge("退货政策", rag=rag, filters=SearchFilters())
