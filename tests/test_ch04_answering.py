import json
from dataclasses import replace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mewhelp.db.base import Base
from mewhelp.knowledge.filters import SearchFilters
from mewhelp.knowledge.query import QueryUnderstanding
from mewhelp.knowledge.refusals import LowConfidenceQuestion, PoolCommitError
from mewhelp.knowledge.retrieval import RankedChunk, RetrievalResult
from mewhelp.knowledge.store import ChunkSnapshot


def evidence(count=1):
    chunks = [
        ChunkSnapshot(
            9223372036854770000 + i,
            "问题：蓝牙\n答案：蓝牙5.3",
            "蓝牙版本",
            "蓝牙5.3",
            "",
            "参数",
            None,
            "faq",
            False,
            "a" * 64,
        )
        for i in range(count)
    ]
    return RetrievalResult(chunks, [RankedChunk(c, 0.9 - i * 0.01) for i, c in enumerate(chunks)])


def inputs():
    from mewhelp.knowledge.answering import QuestionContext

    return QueryUnderstanding(
        "蓝牙啥版本", "蓝牙版本", "蓝牙版本", "knowledge", []
    ), QuestionContext("蓝牙啥版本", None, "cli")


def runtime(assessment, *, budget=30000):
    from mewhelp.knowledge.answering import RagRuntime

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = lambda: Session(engine)
    seen = []

    async def generate(messages):
        seen.append(messages)
        if isinstance(assessment, Exception):
            raise assessment
        return assessment

    return RagRuntime(None, generate, factory, 0.5, budget), seen


def test_layout_is_rank_numbers_with_top_two_at_opposite_ends():
    from mewhelp.knowledge.answering import layout_indices

    assert layout_indices(10) == [1, 3, 5, 7, 9, 10, 8, 6, 4, 2]
    assert layout_indices(3) == [1, 3, 2]
    assert layout_indices(0) == []


@pytest.mark.asyncio
async def test_citations_use_snapshot_bigint_strings_and_deduplicate_before_layout():
    from mewhelp.knowledge.answering import AnswerAssessment, answer_question

    rt, seen = runtime(
        AnswerAssessment(
            answerable=True, reason="充分", answer="蓝牙5.3。[1]", citation_numbers=[1]
        )
    )
    ev = evidence(3)
    ev = RetrievalResult(ev.candidates, [*ev.final, ev.final[0]])
    q, c = inputs()
    out = await answer_question(rt, q, filters=SearchFilters(), context=c, evidence=ev)
    assert not out.refused and len(out.sources) == 3
    assert out.sources[0].chunk_id == "9223372036854770000"
    assert out.sources[0].source_url == "/kb/source/9223372036854770000"
    assert out.sources[0].section_path == "faq / 参数 / 蓝牙版本"
    payload = json.loads(seen[0][-1].content)
    assert [item["number"] for item in payload["evidence"]] == [1, 3, 2]
    assert payload["evidence"][0]["answer"] == "蓝牙5.3"


@pytest.mark.parametrize(
    ("answerable", "answer", "numbers", "reason_code"),
    [
        (True, "猜测的答案[9]", [9], "invalid_citation"),
        (True, "蓝牙5.3", [1], "invalid_citation"),
        (True, "蓝牙5.3[1]", [], "invalid_citation"),
        (True, "蓝牙5.3[1]", [1, 1], "invalid_citation"),
        (True, "", [], "invalid_generation"),
        (False, "猜测的答案[1]", [1], "insufficient_evidence"),
    ],
)
@pytest.mark.asyncio
async def test_invalid_or_insufficient_generation_is_not_exposed_and_records_once(
    answerable, answer, numbers, reason_code
):
    from mewhelp.knowledge.answering import AnswerAssessment, answer_question

    rt, _ = runtime(
        AnswerAssessment(
            answerable=answerable, reason="样例判定", answer=answer, citation_numbers=numbers
        )
    )
    q, c = inputs()
    out = await answer_question(rt, q, filters=SearchFilters(), context=c, evidence=evidence())
    assert out.refused and "猜测的答案" not in out.answer and out.sources == []
    with rt.session_factory() as session:
        rows = session.scalars(select(LowConfidenceQuestion)).all()
        assert len(rows) == 1 and rows[0].reason_code == reason_code
        assert rows[0].original_question == q.original and rows[0].trigger_stage == "generation"
        assert str(rows[0].id) == out.low_confidence_question_id


@pytest.mark.parametrize(
    ("ev", "reason_code"),
    [
        (RetrievalResult([], []), "no_evidence"),
        (replace(evidence(), final=[RankedChunk(evidence().candidates[0], 0.1)]), "low_relevance"),
    ],
)
@pytest.mark.asyncio
async def test_retrieval_refusals_skip_generation(ev, reason_code):
    from mewhelp.knowledge.answering import answer_question

    rt, seen = runtime(None)
    q, c = inputs()
    out = await answer_question(rt, q, filters=SearchFilters(), context=c, evidence=ev)
    assert out.refused and seen == []
    with rt.session_factory() as session:
        assert session.scalar(select(LowConfidenceQuestion)).reason_code == reason_code


@pytest.mark.asyncio
async def test_long_evidence_refuses_without_truncation_or_generation():
    from mewhelp.knowledge.answering import answer_question

    rt, seen = runtime(None, budget=20)
    q, c = inputs()
    out = await answer_question(rt, q, filters=SearchFilters(), context=c, evidence=evidence())
    assert out.refused and seen == []
    with rt.session_factory() as session:
        assert (
            session.scalar(select(LowConfidenceQuestion)).reason_code == "unsupported_context_size"
        )


@pytest.mark.asyncio
async def test_parsing_failure_records_but_provider_error_does_not():
    from mewhelp.knowledge.answering import answer_question

    q, c = inputs()
    rt, _ = runtime(None)
    out = await answer_question(rt, q, filters=SearchFilters(), context=c, evidence=evidence())
    assert out.refused
    rt, _ = runtime(ConnectionError("provider unavailable"))
    with pytest.raises(ConnectionError):
        await answer_question(rt, q, filters=SearchFilters(), context=c, evidence=evidence())
    with rt.session_factory() as session:
        assert session.scalar(select(LowConfidenceQuestion)) is None


@pytest.mark.asyncio
async def test_pool_failure_is_observable_and_no_refusal_result_is_returned():
    from mewhelp.knowledge.answering import answer_question

    rt, _ = runtime(None)
    q, c = inputs()

    def failed():
        raise OSError("DB unavailable")

    rt = replace(rt, session_factory=failed)
    with pytest.raises(PoolCommitError):
        await answer_question(
            rt, q, filters=SearchFilters(), context=c, evidence=RetrievalResult([], [])
        )


@pytest.mark.asyncio
async def test_ablation_explicitly_disables_gate_and_online_pool():
    from mewhelp.knowledge.answering import AnswerAssessment, answer_question

    rt, seen = runtime(
        AnswerAssessment(answerable=True, reason="充分", answer="蓝牙5.3[1]", citation_numbers=[1])
    )
    ev = evidence()
    ev = replace(ev, final=[RankedChunk(ev.candidates[0], None)])
    q, c = inputs()
    out = await answer_question(
        rt,
        q,
        filters=SearchFilters(),
        context=c,
        evidence=ev,
        strategy="bm25",
        apply_relevance_gate=False,
        record_pool=False,
    )
    assert not out.refused and len(seen) == 1
    with rt.session_factory() as session:
        assert session.scalar(select(LowConfidenceQuestion)) is None


def test_threshold_requires_complete_matching_calibration_metadata(tmp_path):
    from mewhelp.knowledge.answering import load_relevance_threshold

    meta = {
        "model_id": "BAAI/bge-reranker-v2-m3",
        "revision": "abc",
        "max_length": 8192,
        "score_transform": "sigmoid",
    }
    path = tmp_path / "calibration.json"
    doc = {
        "threshold": 0.5,
        "model_metadata": meta,
        "corpus_hash": "a" * 64,
        "query_hash": "b" * 64,
    }
    path.write_text(json.dumps(doc))
    assert load_relevance_threshold(path, meta) == 0.5
    for broken in (
        {**doc, "threshold": float("nan")},
        {**doc, "threshold": True},
        {**doc, "query_hash": ""},
        {**doc, "model_metadata": {**meta, "revision": "wrong"}},
    ):
        path.write_text(json.dumps(broken))
        with pytest.raises(ValueError):
            load_relevance_threshold(path, meta)


@pytest.mark.asyncio
async def test_uncited_second_fact_is_rejected():
    from mewhelp.knowledge.answering import AnswerAssessment, answer_question

    rt, _ = runtime(
        AnswerAssessment(
            answerable=True, reason="充分", answer="蓝牙5.3[1]。还支持防水。", citation_numbers=[1]
        )
    )
    q, c = inputs()
    out = await answer_question(rt, q, filters=SearchFilters(), context=c, evidence=evidence())
    assert out.refused


@pytest.mark.asyncio
async def test_oversized_reranker_pair_uses_the_same_refusal_exit(monkeypatch):
    from mewhelp.knowledge import answering
    from mewhelp.knowledge.reranking import UnsupportedContextError

    rt, _ = runtime(None)
    q, c = inputs()

    def overlong(*args, **kwargs):
        raise UnsupportedContextError("pair exceeds 8192")

    monkeypatch.setattr(answering, "retrieve_evidence", overlong)
    out = await answering.answer_question(rt, q, filters=SearchFilters(), context=c)
    assert out.refused
    with rt.session_factory() as session:
        rows = session.scalars(select(LowConfidenceQuestion)).all()
        assert len(rows) == 1 and rows[0].reason_code == "unsupported_context_size"
        assert rows[0].trigger_stage == "retrieval"


@pytest.mark.asyncio
async def test_structured_generation_preserves_raw_envelope_validation():
    from mewhelp.knowledge.answering import generate_assessment

    class Invalid:
        async def ainvoke(self, messages):
            return {
                "parsed": {
                    "answerable": "yes",
                    "answer": "bad",
                    "reason": "x",
                    "citation_numbers": [1],
                },
                "parsing_error": None,
            }

    assert await generate_assessment([], model=Invalid()) is None
