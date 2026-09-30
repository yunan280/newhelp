"""query_faq keeps keyword-only schema and returns trusted hybrid evidence artifacts."""

from langchain_core.utils.function_calling import convert_to_openai_tool
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mewhelp.db.base import Base
from mewhelp.knowledge.answering import RagRuntime
from mewhelp.knowledge.query import QueryUnderstanding
from mewhelp.knowledge.retrieval import RankedChunk, RetrievalRuntime
from mewhelp.knowledge.store import KnowledgeDraft, put_chunk, snapshot_chunk
from mewhelp.knowledge.vectors import SearchHit
from mewhelp.tools.knowledge import build_knowledge_tools


class HybridIndex:
    def __init__(self, chunk):
        self.hits = [SearchHit(chunk.id, chunk.content_hash)]
        self.seen = None
        self.calls = 0

    def search(self, strategy, **kwargs):
        assert strategy == "hybrid_rerank"
        self.seen = kwargs["vector"]
        self.calls += 1
        return self.hits


def _tool():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = lambda: Session(engine)
    with factory() as session:
        row = put_chunk(
            session,
            KnowledgeDraft("faq:shipping", "物流", "运费怎么计算", "单笔满 99 元包邮。", "", "faq"),
        )
        row.vectorize_status = "done"
        row.vector_id = str(row.id)
        chunk = snapshot_chunk(row)
        session.commit()
    index = HybridIndex(chunk)

    async def forbidden_generation(messages):
        raise AssertionError("query_faq cannot generate an answer")

    retrieval = RetrievalRuntime(
        factory,
        lambda texts: [[0.5] * 1024],
        index,
        lambda q, chunks: [RankedChunk(c, 0.9) for c in chunks],
    )
    runtime = RagRuntime(retrieval, forbidden_generation, factory, 0.5, 30000)
    query = QueryUnderstanding("邮费是多少", "邮费是多少", "邮费是多少 满额免邮", "knowledge", [])
    return build_knowledge_tools(factory, rag_runtime=runtime, query=query)[0], index


async def invoke(tool):
    return await tool.ainvoke(
        {"name": tool.name, "args": {"keyword": "邮费是多少"}, "id": "call-1", "type": "tool_call"}
    )


async def test_shipping_paraphrase_returns_hybrid_evidence():
    tool, index = _tool()
    message = await invoke(tool)
    assert "99 元包邮" in message.content
    assert message.artifact.final[0].chunk.answer == "单笔满 99 元包邮。"
    assert index.seen == [0.5] * 1024


async def test_stale_mysql_row_never_becomes_an_artifact():
    tool, index = _tool()
    index.hits = [SearchHit(index.hits[0].id + 1000, index.hits[0].content_hash)]
    message = await invoke(tool)
    assert message.artifact.candidates == message.artifact.final == []
    assert "99 元" not in message.content


async def test_stale_hits_inside_the_fixed_fifty_do_not_hide_a_valid_hit():
    tool, index = _tool()
    index.hits = [SearchHit(i, "z" * 64) for i in range(1000, 1010)] + index.hits
    message = await invoke(tool)
    assert "99 元包邮" in message.content and index.calls == 1


async def test_no_unbounded_expansion_beyond_user_fixed_top_fifty():
    tool, index = _tool()
    index.hits = [SearchHit(i, "z" * 64) for i in range(1000, 1050)] + index.hits
    message = await invoke(tool)
    assert message.artifact.final == [] and index.calls == 1


def test_only_keyword_is_visible_in_tool_schema():
    tool, _ = _tool()
    schema = convert_to_openai_tool(tool)
    assert schema["function"]["name"] == "query_faq"
    assert set(schema["function"]["parameters"]["properties"]) == {"keyword"}
