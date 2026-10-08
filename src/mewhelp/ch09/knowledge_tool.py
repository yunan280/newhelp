"""A business-routed FAQ call still crosses the same formal knowledge boundary."""
import asyncio
import json
from dataclasses import replace

from langchain_core.tools import tool

from mewhelp.ch05.evidence import EvidenceEnvelope, limit_evidence, retrieve_knowledge
from mewhelp.knowledge.filters import SearchFilters
from mewhelp.tools.contracts import ToolSnapshot


def eligible(snapshot, context):
    spec = snapshot.get('query_faq')
    return (getattr(context, 'confidence_profile', None) is not None and spec is not None
            and spec.source == 'builtin' and spec.permission == 'readonly')


def bind_knowledge_tool(snapshot, state, context):
    if not eligible(snapshot, context):
        return snapshot

    @tool(response_format='content_and_artifact')
    async def query_faq(keyword: str) -> tuple[str, EvidenceEnvelope]:
        """检索本轮问题的知识证据；keyword 可填完整问法，结果不能直接当最终答案。"""
        rag = await asyncio.to_thread(context.rag_factory)
        evidence = await retrieve_knowledge(state['resolved_question'], rag=rag,
            filters=SearchFilters.model_validate(state.get('filters', {})))
        evidence = limit_evidence(evidence, top_k=context.confidence_profile.top_k)
        # Full original text travels in the typed artifact and formal snapshot.
        # The tool message is a receipt, not another unbounded evidence payload.
        return json.dumps({'retrieved_count': len(evidence.sources),
                           'next_step': '正式置信度校验后生成回答'}, ensure_ascii=False), evidence

    specs = dict(snapshot.specs)
    specs['query_faq'] = replace(specs['query_faq'], tool_factory=lambda _: query_faq)
    return ToolSnapshot(specs, snapshot.fingerprint)


def knowledge_result_patch(snapshot, result, state, context):
    if (not eligible(snapshot, context) or result.name != 'query_faq' or not result.ok
            or not isinstance(result.artifact, EvidenceEnvelope)):
        return {}
    from mewhelp.ch05.workflow import retrieval_patch

    return {**retrieval_patch(state, result.artifact, context, kind='knowledge_tool'),
            'route': 'knowledge', 'knowledge_tool_gate_pending': True}
