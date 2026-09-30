"""Concrete reproductions from the one fresh Ch04 final review."""

import json

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateIndex, CreateTable

from mewhelp.ch02 import service
from mewhelp.knowledge.answering import AnswerAssessment, RagRuntime
from mewhelp.knowledge.evaluation.dataset import load_dataset
from mewhelp.knowledge.ingest import ingest_documents
from mewhelp.knowledge.query import QueryUnderstanding, validate_normalization
from mewhelp.knowledge.refusals import LowConfidenceQuestion
from mewhelp.knowledge.reranking import UnsupportedContextError
from mewhelp.knowledge.retrieval import RankedChunk, RetrievalRuntime
from mewhelp.knowledge.sources import read_document_source
from mewhelp.knowledge.store import KnowledgeChunk, snapshot_chunk
from mewhelp.knowledge.vectors import SearchHit
from tests.test_ch04_chat import (
    chat_runtime as chat_runtime,  # noqa: PLC0414 — pytest fixture re-export
)
from tests.test_ch04_eval_runner import ROOT, Index
from tests.test_ch04_migration import migration, old_engine


@pytest.mark.parametrize(
    "original,proposed",
    [
        ("容量5Ah的电池能用吗？", "容量5A的电池能用吗？"),
        ("支持65Wh吗？", "支持65W吗？"),
        ("保修期内可以免费维修吗？", "可以免费维修吗？"),
        ("除非拆封，否则7天内能退吗？", "拆封7天内能退吗？"),
        ("HX-210不支持65W但支持10W吗？", "HX-210支持65W但不支持10W吗？"),
        ("HX-210支持蓝牙吗？", "HX-210不支持蓝牙吗？"),
    ],
)
def test_review_sensitive_facts_cannot_change(original, proposed):
    result = validate_normalization(
        original, {"canonical": proposed, "synonyms": [], "route": "knowledge"}
    )
    assert result.canonical == original
    assert "protected_information_changed" in result.diagnostics


def test_review_synonyms_cannot_change_unit_or_negative_scope():
    original = "支持65Wh吗？"
    result = validate_normalization(
        original,
        {
            "canonical": original,
            "synonyms": ["65W", "不支持65Wh", "能量规格"],
            "route": "knowledge",
        },
    )
    assert "65W " not in result.bm25_query and "不支持" not in result.bm25_query
    assert "能量规格" in result.bm25_query


def test_review_unknown_chinese_unit_cannot_be_changed_by_synonym():
    original = "能走1公里吗？"
    result = validate_normalization(
        original, {"canonical": original, "synonyms": ["1公斤"], "route": "knowledge"}
    )
    assert "1公斤" not in result.bm25_query


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.asyncio
async def test_review_mixed_business_cannot_release_uncited_parameters(
    chat_runtime, monkeypatch, stream
):
    from tests.fakes import FakeToolChatModel, text_chunks, tool_call_chunks

    factory, _, state, _ = chat_runtime
    original = "订单1001的状态和HX-210的蓝牙版本？"

    async def understand(question):
        # Exercise actual validation, including a falsely confident business label.
        return validate_normalization(
            question,
            {"canonical": question, "synonyms": [], "route": "business"},
        )

    monkeypatch.setattr(service, "understand_query", understand)
    model = FakeToolChatModel(
        rounds=[
            tool_call_chunks("query_order", '{"order_id":"1001"}'),
            text_chunks("订单已发货。HX-210支持蓝牙9.9。"),
        ]
    )
    monkeypatch.setattr(service, "get_chat_model", lambda: model)
    state["assessment"] = AnswerAssessment(
        answerable=False,
        reason="知识证据没有订单状态，不能完整回答",
        answer="",
        citation_numbers=[],
    )
    if stream:
        events = [
            item
            async for item in service.stream_agent_turn(
                factory, session_id="mixed-stream", user_id="u", message=original
            )
        ]
        assert any(type(item).__name__ == "SourcesEvent" and item.refused for item in events)
        assert "9.9" not in "".join(getattr(item, "text", "") for item in events)
    else:
        result = await service.run_agent_turn(
            factory, session_id="mixed-json", user_id="u", message=original
        )
        assert result.refused and result.sources == [] and "9.9" not in result.answer
    assert state["generate"] == 1


def test_review_business_requires_explicit_whole_turn_confirmation():
    raw = {"canonical": "订单1001状态？", "synonyms": [], "route": "business"}
    assert validate_normalization(raw["canonical"], raw).route == "knowledge"
    assert (
        validate_normalization(raw["canonical"], {**raw, "business_only": True}).route == "business"
    )


@pytest.mark.parametrize(
    "markdown",
    [
        "# 售后说明\n## 退款\n请凭订单提交申请。\n",
        "# 售后说明\n## 退款表\n|条件|操作|\n|---|---|\n|未拆封|提交申请|\n",
    ],
)
def test_review_real_generic_markdown_ingest_opens_original(tmp_path, markdown):
    from sqlalchemy import create_engine

    from mewhelp.db.base import Base

    (tmp_path / "refunds.md").write_text(markdown, encoding="utf-8")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        assert ingest_documents(session, tmp_path) > 0
        for row in session.scalars(select(KnowledgeChunk)).all():
            source = read_document_source(snapshot_chunk(row), tmp_path)
            assert source is not None
            assert source.filename == "refunds.md" and source.markdown == markdown
    engine.dispose()


@pytest.mark.asyncio
async def test_review_faq_context_limit_refuses_once_and_caches_domain_outcome(
    chat_runtime, monkeypatch
):
    from pathlib import Path
    from types import SimpleNamespace

    from mewhelp.tools import knowledge
    from tests.fakes import FakeToolChatModel, tool_call_chunks

    factory, runtime, state, _ = chat_runtime
    count = []

    def too_long(*args, **kwargs):
        count.append(True)
        raise UnsupportedContextError("pair exceeds 8192 tokens")

    monkeypatch.setattr(knowledge, "retrieve_evidence", too_long)
    monkeypatch.setattr(knowledge, "get_rag_runtime", lambda *a, **kw: runtime)
    monkeypatch.setattr(
        knowledge,
        "get_settings",
        lambda: SimpleNamespace(rag_calibration_path=Path("calibration.json")),
    )

    async def business(question, **kwargs):
        return QueryUnderstanding(question, question, question, "business", [])

    monkeypatch.setattr(service, "understand_query", business)
    model = FakeToolChatModel(
        rounds=[
            tool_call_chunks("query_faq", '{"keyword":"政策"}', call_id="one", index=0)
            + tool_call_chunks("query_faq", '{"keyword":"政策"}', call_id="two", index=1)
        ]
    )
    monkeypatch.setattr(service, "get_chat_model", lambda: model)
    result = await service.run_agent_turn(
        factory, session_id="faq-too-long", user_id="u", message="订单1001的退货政策？"
    )
    assert result.refused and result.sources == [] and count == [True]
    assert state["generate"] == 0
    assert result.tool_results[0].artifact is result.tool_results[1].artifact
    with factory() as session:
        rows = session.scalars(select(LowConfidenceQuestion)).all()
        assert len(rows) == 1 and rows[0].reason_code == "unsupported_context_size"
        assert rows[0].trigger_stage == "retrieval"


def test_review_pool_without_primary_key_rejected_before_migration():
    engine = old_engine()
    ddl = str(CreateTable(LowConfidenceQuestion.__table__).compile(dialect=engine.dialect))
    ddl = ddl.replace("PRIMARY KEY (id),", "")
    with engine.begin() as connection:
        connection.exec_driver_sql(ddl)
        for index in LowConfidenceQuestion.__table__.indexes:
            connection.exec_driver_sql(str(CreateIndex(index).compile(dialect=engine.dialect)))
    with pytest.raises(RuntimeError, match="primary key"):
        migration().migrate_ch04(engine)
    assert "citations" not in {column["name"] for column in inspect(engine).get_columns("messages")}


def test_review_mysql_pool_without_autoincrement_rejected(monkeypatch):
    from types import SimpleNamespace

    from sqlalchemy.dialects.mysql import dialect

    engine = old_engine()
    module = migration()
    module.migrate_ch04(engine)
    actual = inspect(engine)

    class Inspector:
        def __getattr__(self, name):
            return getattr(actual, name)

        def get_columns(self, table):
            columns = actual.get_columns(table)
            if table == "low_confidence_questions":
                for column in columns:
                    if column["name"] == "id":
                        column["autoincrement"] = False
                    column["type"] = LowConfidenceQuestion.__table__.c[column["name"]].type
            return columns

    monkeypatch.setattr(module, "inspect", lambda _: Inspector())
    # Compile both desired and reflected types identically, varying only the generation flag.
    with pytest.raises(RuntimeError, match="AUTO_INCREMENT"):
        module._validate_existing(SimpleNamespace(dialect=dialect()))
    assert dialect().name == "mysql"


@pytest.mark.asyncio
async def test_review_generation_failure_retains_real_retrieval_metrics(tmp_path, monkeypatch):
    from mewhelp.knowledge.evaluation import runner

    corpus, cases = load_dataset(ROOT / "corpus.jsonl", ROOT / "queries.jsonl")
    mapping = {case.question: case for case in cases}

    class MatchingIndex(Index):
        def search(self, strategy, **kwargs):
            case = mapping[kwargs["bm25_query"]]
            return [
                SearchHit(item, self.rows[item].content_hash) for item in case.relevant_chunk_ids
            ]

    index = MatchingIndex()
    monkeypatch.setattr(runner, "_new_index", lambda collection: index)
    monkeypatch.setattr(runner, "embed_texts", lambda texts: [[0.0] * 1024 for _ in texts])
    monkeypatch.setattr(runner, "_model_metadata", lambda: {"reranker": {}, "embedding": {}})

    async def normalize(question):
        return QueryUnderstanding(question, question, question, "knowledge", [])

    async def failed_generation(messages):
        raise ConnectionError("provider unavailable after successful retrieval")

    def runtime(factory, index):
        retrieval = RetrievalRuntime(
            factory,
            lambda texts: [[0.0] * 1024],
            index,
            lambda q, chunks: [RankedChunk(c, 0.9) for c in chunks],
        )
        return RagRuntime(retrieval, failed_generation, factory, 0, 32000)

    monkeypatch.setattr(runner, "understand_query", normalize)
    monkeypatch.setattr(runner, "_runtime", runtime)
    await runner.prepare_run(
        corpus, cases, workdir=tmp_path, collection="ch04_eval_review", run_id="review"
    )
    await runner.run_comparison(
        corpus, cases, workdir=tmp_path, collection="ch04_eval_review", run_id="review"
    )
    report = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    for strategy in report["strategies"].values():
        stats = strategy["overall"]
        assert stats["errors"] == 32 and stats["candidate_recall50_N"] == 32
        assert stats["candidate_recall50"] == stats["final_mrr10"] == 1
        assert stats["faithfulness"] is None and stats["answers"] == 0
    rows = [
        json.loads(line)
        for line in (tmp_path / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert all(row["candidate_ids"] for row in rows if not row["should_refuse"])
