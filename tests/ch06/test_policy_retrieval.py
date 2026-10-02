import asyncio
import math
from importlib import import_module
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mewhelp.ch06.config import PolicyCalibration
from mewhelp.ch06.orders import load_demo_order
from mewhelp.db.base import Base
from mewhelp.knowledge.filters import SearchFilters
from mewhelp.knowledge.query import QueryUnderstanding
from mewhelp.knowledge.retrieval import RankedChunk, RetrievalRuntime
from mewhelp.knowledge.store import KnowledgeChunk, KnowledgeDraft, put_chunk, snapshot_chunk
from mewhelp.knowledge.vectors import SearchHit


def environment():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = lambda: Session(engine)
    chunks = []
    with factory() as session:
        for i in range(8):
            row = put_chunk(session, KnowledgeDraft(
                f"policy:{i}", "退款售后政策" if i < 7 else "配送",
                f"条款{i}", f"规则{i}", f"退款售后政策/条款{i}", "policy",
            ))
            row.vectorize_status, row.vector_id = "done", str(row.id)
            chunks.append(snapshot_chunk(row))
        for i, change in [(2, "pending"), (3, "deleted"), (4, "changed")]:
            row = session.get(KnowledgeChunk, chunks[i].id)
            if change == "pending":
                row.vectorize_status = "pending"
            elif change == "deleted":
                row.section_path = "__deleting__::条款"
            else:
                row.answer = "更新后的规则"
        session.commit()
    calls = {"search": [], "rerank": []}

    class Index:
        def search(self, strategy, **kw):
            calls["search"].append((strategy, kw))
            ranks = [1, 0, 2, 3, 4, 7, 1] if len(calls["search"]) == 1 else [0, 1, 5, 6, 7]
            return [SearchHit(chunks[i].id, chunks[i].content_hash) for i in ranks]

    def rerank(question, items):
        calls["rerank"].append((question, items))
        return [RankedChunk(item, .8) for item in items]

    return RetrievalRuntime(factory, lambda texts: [[.1] * 1024 for t in texts],
                            Index(), rerank), chunks, calls


def multi():
    try:
        return import_module("mewhelp.knowledge.retrieval").retrieve_multi_evidence
    except AttributeError:
        pytest.fail("missing multi-query retrieval")


def test_multi_query_rrf_validates_authority_and_reranks_once():
    runtime, chunks, calls = environment()
    queries = [QueryUnderstanding(q, q, q, "knowledge", []) for q in ["原问题", "资格条件", "例外"]]
    result = multi()(runtime, queries,
                     SearchFilters(content_type="policy", category="退款售后政策"),
                     rerank_question="本单退款资格")
    ids = [item.chunk.id for item in result.final]
    assert ids == [chunks[0].id, chunks[1].id, chunks[5].id, chunks[6].id]
    assert len(ids) == len(set(ids)) and len(calls["rerank"]) == 1
    assert len(calls["search"]) == 3
    assert all(s == "hybrid" and kw["filters"].content_type == "policy"
               for s, kw in calls["search"])


def calibration(runtime, monkeypatch):
    from mewhelp.ch05 import evidence
    if not hasattr(evidence, "policy_corpus_hash"):
        pytest.fail("missing policy calibration binding")
    metadata = {"model_id": "BAAI/bge-reranker-v2-m3", "revision": "fixture",
                "max_length": 8192, "score_transform": "sigmoid"}
    monkeypatch.setattr(evidence, "reranker_metadata", lambda: metadata)
    return PolicyCalibration(
        policy_rerank_threshold=.7, reranker_metadata=metadata,
        corpus_hash=evidence.policy_corpus_hash(runtime.session_factory),
        retrieval_input_hash=evidence.policy_input_hash(),
        dataset_hash=evidence.policy_dataset_hash(), sample_count=16,
    )


def test_fallback_original_still_forces_policy_and_renumbers_sources(monkeypatch):
    from mewhelp.ch05 import evidence
    runtime, _, calls = environment()
    cal = calibration(runtime, monkeypatch)
    result = asyncio.run(evidence.retrieve_policy(
        "本单能退吗", load_demo_order("demo-user", "1001"), ["本单能退吗"],
        rag=SimpleNamespace(retrieval=runtime, context_budget=10000),
        filters=SearchFilters(), calibration=cal,
    ))
    assert result.threshold == .7 and result.sources
    assert [s.number for s in result.sources] == list(range(1, len(result.sources) + 1))
    assert len(calls["search"]) == 1


@pytest.mark.parametrize("filters", [SearchFilters(category="配送"),
                                     SearchFilters(content_type="faq"),
                                     SearchFilters(is_key_clause=True)])
def test_conflicting_filter_is_rejected_before_retrieval(filters, monkeypatch):
    from mewhelp.ch05 import evidence
    runtime, _, calls = environment()
    cal = calibration(runtime, monkeypatch)
    with pytest.raises(ValueError, match="policy"):
        asyncio.run(evidence.retrieve_policy(
            "能退吗", load_demo_order("demo-user", "1001"), [],
            rag=SimpleNamespace(retrieval=runtime, context_budget=10000),
            filters=filters, calibration=cal,
        ))
    assert calls["search"] == []


def test_stale_policy_calibration_cannot_authorize_answer(monkeypatch):
    from mewhelp.ch05 import evidence
    runtime, _, calls = environment()
    cal = calibration(runtime, monkeypatch).model_copy(update={"corpus_hash": "0" * 64})
    with pytest.raises(ValueError, match="calibration"):
        asyncio.run(evidence.retrieve_policy(
            "能退吗", load_demo_order("demo-user", "1001"), [],
            rag=SimpleNamespace(retrieval=runtime, context_budget=10000),
            filters=SearchFilters(), calibration=cal,
        ))
    assert calls["search"] == []


def test_reject_all_threshold_is_representable():
    cal = PolicyCalibration(policy_rerank_threshold=math.nextafter(1., math.inf),
                            reranker_metadata={}, corpus_hash="a" * 64,
                            retrieval_input_hash="b" * 64, dataset_hash="c" * 64,
                            sample_count=16)
    assert cal.policy_rerank_threshold > 1


async def test_calibration_measures_real_fallback_instead_of_dropping_case(tmp_path, monkeypatch):
    from mewhelp.ch06 import policy_evaluation as pe
    from mewhelp.ch06.expansion import ExpansionResult

    runtime, chunks, _ = environment()
    metadata = {"model_id": "BAAI/bge-reranker-v2-m3", "revision": "fixture",
                "max_length": 8192, "score_transform": "sigmoid"}
    monkeypatch.setattr(pe, "verify_dataset", lambda p: {})
    monkeypatch.setattr(pe, "policy_dataset_hash", lambda: "c" * 64)
    monkeypatch.setattr(pe, "reranker_metadata", lambda: metadata)
    monkeypatch.setattr(pe, "_read_cases", lambda p: [
        {"id": f"p-{i}", "question": f"问题{i}", "expected": {"relevant": i < 8}}
        for i in range(16)
    ])
    async def fallback(question, *a, **kw):
        return ExpansionResult(True, [question], ["invented_expansion_fact"])
    monkeypatch.setattr(pe, "expand_queries", fallback)
    seen = []
    def retrieve(rt, queries, *a, **kw):
        seen.append(queries[0].canonical)
        return SimpleNamespace(final=[RankedChunk(chunks[0], .8)])
    monkeypatch.setattr(pe, "retrieve_multi_evidence", retrieve)
    assert await pe.calibrate_policy(tmp_path, tmp_path / "out", runtime) == 0
    assert len(seen) == 16
