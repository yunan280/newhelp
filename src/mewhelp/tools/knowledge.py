"""One trusted, cached retrieval artifact per turn; tools never generate or record refusals."""

import asyncio
import json
from collections.abc import Callable

from langchain_core.tools import BaseTool, tool
from sqlalchemy.orm import Session

from mewhelp.config import get_settings
from mewhelp.knowledge.answering import QuestionContext, RagRuntime, get_rag_runtime
from mewhelp.knowledge.embedding import embed_texts
from mewhelp.knowledge.filters import SearchFilters
from mewhelp.knowledge.query import QueryUnderstanding, understand_query
from mewhelp.knowledge.reranking import UnsupportedContextError
from mewhelp.knowledge.retrieval import RetrievalResult, retrieve_evidence


def build_knowledge_tools(
    session_factory: Callable[[], Session],
    *,
    embed=embed_texts,
    vectors=None,
    context: QuestionContext | None = None,
    filters: SearchFilters | None = None,
    rag_runtime: RagRuntime | None = None,
    query: QueryUnderstanding | None = None,
) -> list[BaseTool]:
    lock = asyncio.Lock()
    cached: RetrievalResult | None = None
    trusted_filters = filters or SearchFilters()

    @tool(response_format="content_and_artifact")
    async def query_faq(keyword: str) -> tuple[str, RetrievalResult]:
        """检索本轮问题的知识证据；keyword 可填完整问法，结果不能直接当最终答案。"""
        nonlocal cached
        async with lock:
            if cached is None:
                current = query
                if current is None:
                    current = await understand_query(
                        context.original_question if context else keyword
                    )
                runtime = rag_runtime
                if runtime is None:
                    path = get_settings().rag_calibration_path
                    if path is None:
                        raise RuntimeError(
                            "RAG_CALIBRATION_PATH must be configured for knowledge queries"
                        )
                    runtime = await asyncio.to_thread(
                        get_rag_runtime, session_factory, calibration_path=path
                    )
                retrieval = runtime.retrieval
                if vectors is not None:
                    from dataclasses import replace

                    retrieval = replace(retrieval, embed=embed, index=vectors)
                try:
                    cached = await asyncio.to_thread(
                        retrieve_evidence, retrieval, current, trusted_filters
                    )
                except UnsupportedContextError as exc:
                    # Domain refusal is a cached typed artifact; infrastructure failures still raise.
                    cached = RetrievalResult([], [], unsupported_context_reason=str(exc))
        content = json.dumps(
            [
                {
                    "chunk_id": str(item.chunk.id),
                    "questions": item.chunk.questions,
                    "answer": item.chunk.answer,
                    "section_path": item.chunk.section_path,
                }
                for item in cached.final
            ],
            ensure_ascii=False,
        )
        return content, cached

    return [query_faq]
