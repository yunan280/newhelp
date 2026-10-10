"""单轮客服编排：可信 Query 路由、业务工具与有据知识回答。

两个出口共用一个核心:
- `stream_agent_turn` 逐 token 流式(SSE 用)
- `run_agent_turn` 一次性返回(JSON / eval / 测试用)
差别只在**收敛那一步**,前六步完全一样,所以抽成 `_prepare_turn`。

`model.astream(...)` 这一层只产出事件对象,不关心 SSE 的帧格式 ——
传输格式归 api 层(ch01 定下的边界)。
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field, replace
from uuid import uuid4

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mewhelp.ch01.service import EmptyCompletionError
from mewhelp.ch09.observability import current_request, model_kwargs, trace_legacy
from mewhelp.config import HISTORY_TOKEN_BUDGET, get_settings
from mewhelp.db.repository import (
    TurnMessage,
    append_messages,
    get_or_create_conversation,
    load_replay_messages,
)
from mewhelp.knowledge.answering import (
    AnswerResult,
    QuestionContext,
    SourceDTO,
    answer_question,
    get_rag_runtime,
)
from mewhelp.knowledge.filters import SearchFilters
from mewhelp.knowledge.query import QueryUnderstanding, requires_knowledge, understand_query
from mewhelp.llm import get_chat_model
from mewhelp.memory import store, trim_history
from mewhelp.tools.contracts import ToolCallContext
from mewhelp.tools.infra import ToolResult
from mewhelp.tools.registry import ToolRegistry
from mewhelp.tools.ticket import build_registry

from .events import (
    AgentEvent,
    DoneEvent,
    SessionEvent,
    SourcesEvent,
    TokenEvent,
    ToolEvent,
    tool_event_from,
)
from .prompts import AGENT_SYSTEM

# 在编排层**重新声明**一次,不从 `db.engine` 引 —— 编排层不该依赖那条 MySQL 连接。
# 它要的只是"能开出一个 Session"这个能力,测试因此可以塞内存库进来。
SessionFactory = Callable[[], Session]


@dataclass
class PreparedTurn:
    """turn1 跑完、收敛之前的全部状态。"""

    session_id: str
    conversation_id: int
    resumed: bool
    messages: list[BaseMessage]  # 送给模型的历史 + 本轮提问(含 system)
    ai: AIMessage  # turn1 的完整返回(可能带 tool_calls)
    registry: ToolRegistry
    tool_results: list[ToolResult]
    query: QueryUnderstanding | None = None
    filters: SearchFilters | None = None
    entry_point: str = "agent"
    call_context: ToolCallContext | None = None

    @property
    def tool_messages(self) -> list[ToolMessage]:
        """把工具结果翻成回灌用的 ToolMessage,tool_call_id 与申请单对号入座。"""
        return [
            ToolMessage(content=result.content, tool_call_id=call["id"], name=result.name,
                        status='success' if result.ok else 'error')
            for call, result in zip(self.ai.tool_calls, self.tool_results, strict=True)
        ]


def _resolve_conversation(session: Session, *, session_id: str, user_id: str):
    """建/取会话。并发撞 UNIQUE 时重查一次。

    ch01 的 store.lock 已经把同一 session 的整轮串行化了,所以这条路径基本
    走不到 —— **但锁是进程内的**,多 worker 之外仍要写对。
    """
    try:
        return get_or_create_conversation(session, session_id=session_id, user_id=user_id)
    except IntegrityError:
        session.rollback()
        conv, _ = get_or_create_conversation(session, session_id=session_id, user_id=user_id)
        return conv, False


def _to_messages(rows) -> list[BaseMessage]:
    """把库里的行翻成 LangChain 消息。只处理回放得到的那两类。"""
    out: list[BaseMessage] = []
    for row in rows:
        if row.role.value == "user":
            out.append(HumanMessage(content=row.content or ""))
        else:
            out.append(AIMessage(content=row.content or ""))
    return out


class _Sink:
    """`_prepare_turn_events` 的出口。

    async generator 不能 `return` 一个值(PEP 525),而两个出口都需要拿到
    `PreparedTurn`。用一个可变占位对象把它递出来,比"产出一个特殊事件"
    干净 —— 后者会把非事件对象塞进 `AgentEvent` 的语义里。
    """

    prepared: PreparedTurn | None = None


async def _prepare_turn_events(
    session_factory: SessionFactory,
    *,
    session_id: str,
    user_id: str,
    message: str,
    sink: _Sink,
    filters: SearchFilters | None = None,
    entry_point: str = "agent",
    tool_runtime=None,
) -> AsyncIterator[AgentEvent]:
    """先提交会话身份，再仅按当前问题路由。

    知识问题交给共享 RAG 核心。问候保留逐块输出；业务前言缓冲，
    只有取得业务工具结果后才进入收敛。工具 start 在执行前发出。
    Session 锁与整轮落库由外层出口负责。
    """
    with session_factory() as session:
        conv, created = _resolve_conversation(session, session_id=session_id, user_id=user_id)
        conversation_id = conv.id
        resumed = not created
        session.commit()

        history_rows = load_replay_messages(session, conversation_id=conversation_id)
        history = trim_history(_to_messages(history_rows), max_tokens=HISTORY_TOKEN_BUDGET)

    root = current_request()
    if root:
        root.bind(session_id=session_id, conversation_id=conversation_id)
    yield SessionEvent(session_id=session_id, resumed=resumed)

    messages: list[BaseMessage] = [
        SystemMessage(content=AGENT_SYSTEM),
        *history,
        HumanMessage(content=message),
    ]

    query = await understand_query(message)
    root = current_request()
    if root:
        root.set_intent(query.route)
    trusted_filters = filters or SearchFilters()
    if tool_runtime is None:
        registry = build_registry(session_factory, conversation_id,
            context=QuestionContext(message, conversation_id, entry_point),
            filters=trusted_filters, query=query)
        from mewhelp.tools.audit import ToolAuditWriter
        from mewhelp.tools.engine import ToolExecutionEngine
        registry.engine = ToolExecutionEngine(ToolAuditWriter(session_factory))
    else:
        snapshot = await tool_runtime.refresh()
        specs = dict(snapshot.specs)
        # Bind the original trusted query/filters to FAQ, rather than a model
        # rewriting the query or weakening metadata filters.
        from mewhelp.tools.knowledge import build_knowledge_tools
        faq = build_knowledge_tools(session_factory,
            context=QuestionContext(message, conversation_id, entry_point),
            filters=trusted_filters, query=query)[0]
        specs['query_faq'] = replace(specs['query_faq'], tool_factory=lambda context:faq)
        registry = ToolRegistry(specs, engine=tool_runtime.engine)
    call_context = ToolCallContext(conversation_id, session_id, user_id)
    if query.route == "knowledge":
        # No ordinary model can produce a factual preamble on this path.
        ai = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "query_faq",
                    "args": {"keyword": message},
                    "id": uuid4().hex,
                    "type": "tool_call",
                }
            ],
        )
        sink.prepared = PreparedTurn(
            session_id,
            conversation_id,
            resumed,
            messages,
            ai,
            registry,
            [],
            query,
            trusted_filters,
            entry_point,
            call_context,
        )
        return

    # turn1 走 astream 而不是 ainvoke:模型既可能只吐工具调用,也可能先说一句
    # 前言再调工具。若走 ainvoke,"不需要工具"的那些轮次就再也流不了式了 ——
    # 而那是**多数**轮次,ch01 的流式会白做(spec §11 的方案 (c))。
    # 分片的 tool_call_chunks 靠 AIMessageChunk 相加拼回完整 tool_calls(已实测)。
    collected: AIMessageChunk | None = None
    async for chunk in get_chat_model().bind_tools(registry.tools()).astream(messages, **model_kwargs()):
        if chunk.text and query.route == "greeting":
            yield TokenEvent(text=chunk.text)
        collected = chunk if collected is None else collected + chunk

    ai = AIMessage(
        content=collected.text if collected is not None else "",
        tool_calls=collected.tool_calls if collected is not None else [],
    )

    results: list[ToolResult] = []
    if ai.tool_calls:
        # start 帧只带 name/args:此刻还没有结果,填上 ok/elapsed_ms 会被前端
        # 读成"这一轮失败了"(见 T11 的 tool_event_from 只用于 end 的原因)。
        for call in ai.tool_calls:
            yield ToolEvent(name=call["name"], args=call["args"], phase="start")

        # 用 asyncio.gather 并发跑:一轮里多个工具是相互独立的读,没有先后依赖。
        # 结果顺序与 tool_calls 一致,靠的是 run_all 内部的 gather 保序。
        results = await registry.run_all(ai.tool_calls, context=call_context)

        for result in results:
            yield tool_event_from(result, phase="end")

    sink.prepared = PreparedTurn(
        session_id=session_id,
        conversation_id=conversation_id,
        resumed=resumed,
        messages=messages,
        ai=ai,
        registry=registry,
        tool_results=results,
        query=query,
        filters=trusted_filters,
        entry_point=entry_point,
        call_context=call_context,
    )


async def _prepare_turn(
    session_factory: SessionFactory,
    *,
    session_id: str,
    user_id: str,
    message: str,
    filters: SearchFilters | None = None,
    tool_runtime=None,
) -> PreparedTurn:
    """turn1:会话身份 → 组装上下文 → 定工具 → 执行工具。

    **不落库** —— 落库只在整轮成功之后(spec §13)。会话壳是例外:它必须先建,
    否则没有 conversation_id 可以绑给 create_ticket。空壳留着无害,
    而且客户端下一轮带着同一个 session_id 回来时能拿到 resumed=True。

    **不加锁、不生成 id** —— 两件事都归调用它的那两个出口(Task 14 / 15),
    不是随手挪的:

    - **锁**要罩住「读历史 → 收敛 → 落库」**整段**。只罩住这里的话,同一 session
      的两轮会各自读到同一份历史、各自收敛,再各写各的 —— 落库顺序交错,而两轮
      都以为自己接住了上下文。用户双击发送、或两个标签页同一个会话就能撞上。
    - **生成 id** 是"客户端没给身份时给一个"的传输层判断;放到这里会让签名叫
      `str | None`,而函数体里再也没有依据判断该不该生成。

    这就是 `_prepare_turn_events` 外面那层薄壳:非流式出口(JSON / eval / 测试)
    只关心最后那个 `PreparedTurn`,把事件丢掉即可。
    """
    sink = _Sink()
    async for _event in _prepare_turn_events(
        session_factory,
        session_id=session_id,
        user_id=user_id,
        message=message,
        sink=sink,
        filters=filters,
        tool_runtime=tool_runtime,
    ):
        pass
    if sink.prepared is None:  # pragma: no cover —— 生成器必然在耗尽前写入
        raise RuntimeError("事件流结束了却没有产出 PreparedTurn")
    return sink.prepared


def build_turn_rows(
    prepared: PreparedTurn, *, answer: str, citations: list[dict] | None = None
) -> list[TurnMessage]:
    """一轮成功后要写的消息行。调了工具写 4 条,没调写 2 条。"""
    from mewhelp.db.models import MsgRole

    rows = [TurnMessage(role=MsgRole.user, content=prepared.messages[-1].content)]
    if prepared.ai.tool_calls:
        rows.append(
            TurnMessage(
                role=MsgRole.assistant,
                content=prepared.ai.content or None,
                tool_calls=list(prepared.ai.tool_calls),
            )
        )
        for call, result in zip(prepared.ai.tool_calls, prepared.tool_results, strict=True):
            rows.append(
                TurnMessage(role=MsgRole.tool, content=result.content, tool_call_id=call["id"])
            )
    rows.append(TurnMessage(role=MsgRole.assistant, content=answer, citations=citations))
    return rows


def persist_turn(
    session_factory: SessionFactory,
    prepared: PreparedTurn,
    *,
    answer: str,
    citations: list[dict] | None = None,
) -> None:
    """整轮成功后一次性落库。

    **失败只记日志,不抛** —— token 已经逐字吐给用户了,这时候因为落库失败
    补一个 error 帧是在骗人:用户明明看到了完整回答,却被告知这一轮失败了。
    代价是账本可能静默缺行,这是本章明确接受的设计代价(spec §13)。
    """
    try:
        with session_factory() as session:
            append_messages(
                session,
                conversation_id=prepared.conversation_id,
                rows=build_turn_rows(prepared, answer=answer, citations=citations),
            )
            session.commit()
    except Exception:  # 落库失败不许推翻已经答完的那一轮
        logging.getLogger(__name__).exception(
            "落库失败,本轮账本缺行(session_id=%s)", prepared.session_id
        )


def _needs_knowledge(prepared: PreparedTurn) -> bool:
    if prepared.query is None:
        return False
    if (prepared.query.route == "knowledge" or requires_knowledge(prepared.query.original)) or any(
        call["name"] == "query_faq" for call in prepared.ai.tool_calls
    ):
        return True
    business_names = set(prepared.registry.names()) - {'query_faq', 'load_order'}
    # A structured failure is trustworthy evidence that the business operation failed.
    return prepared.query.route == "business" and not any(
        result.name in business_names for result in prepared.tool_results
    )


async def _answer_knowledge(
    session_factory: SessionFactory, prepared: PreparedTurn
) -> AnswerResult:
    query = prepared.query
    if query is None:
        raise RuntimeError("trusted current query is missing")
    path = get_settings().rag_calibration_path
    if path is None:
        raise RuntimeError("RAG_CALIBRATION_PATH must be configured for knowledge queries")
    runtime = await asyncio.to_thread(get_rag_runtime, session_factory, calibration_path=path)
    evidence = None
    faq_results = [result for result in prepared.tool_results if result.name == "query_faq"]
    if not faq_results:
        from mewhelp.tools.knowledge import build_knowledge_tools
        faq = build_knowledge_tools(session_factory, query=query, rag_runtime=runtime,
            context=QuestionContext(query.original, prepared.conversation_id, prepared.entry_point),
            filters=prepared.filters or SearchFilters())[0]
        specs = dict(prepared.registry.snapshot().specs)
        specs['query_faq'] = replace(specs['query_faq'], tool_factory=lambda context:faq)
        prepared.registry = ToolRegistry(specs, engine=prepared.registry.engine)
        call = next((c for c in prepared.ai.tool_calls if c['name'] == 'query_faq'), None)
        if call is None:
            call = {'name':'query_faq', 'args':{'keyword':query.original}, 'id':uuid4().hex, 'type':'tool_call'}
            prepared.ai.tool_calls.append(call)
        observation = await prepared.registry.run('query_faq', call['args'],
            context=replace(prepared.call_context or ToolCallContext(conversation_id=prepared.conversation_id), tool_call_id=call['id']))
        prepared.tool_results.append(observation)
        faq_results = [observation]
    if faq_results:
        if any(not item.ok or item.artifact is None for item in faq_results):
            from mewhelp.knowledge.retrieval import RetrievalResult
            return AnswerResult(faq_results[0].content, [], False, None, RetrievalResult([], []))
        evidence = faq_results[0].artifact
    result = await answer_question(
        runtime,
        query,
        filters=prepared.filters or SearchFilters(),
        context=QuestionContext(query.original, prepared.conversation_id, prepared.entry_point),
        evidence=evidence,
    )
    return result


@trace_legacy('ch02_stream', stream=True)
async def stream_agent_turn(
    session_factory: SessionFactory,
    *,
    session_id: str | None,
    user_id: str,
    message: str,
    filters: SearchFilters | None = None,
    tool_runtime=None,
) -> AsyncIterator[AgentEvent]:
    """锁覆盖整轮；知识答案校验后以 sources → token → done 交付。

    问候保持模型逐块流式。业务依据工具结果收敛。服务错误抛给 HTTP 层，
    正常知识拒答先独立提交问题池，随后写消息账本。
    """
    resolved = session_id or uuid4().hex

    async with store.lock(resolved):
        sink = _Sink()
        # 只转发已经按当前路由允许交付的事件。
        async for event in _prepare_turn_events(
            session_factory,
            session_id=resolved,
            user_id=user_id,
            message=message,
            sink=sink,
            filters=filters,
            entry_point="chat_stream",
            tool_runtime=tool_runtime,
        ):
            yield event

        prepared = sink.prepared
        if prepared is None:  # pragma: no cover —— 生成器必然在耗尽前写入
            raise RuntimeError("事件流结束了却没有产出 PreparedTurn")

        if _needs_knowledge(prepared):
            direct = not any(item.name == "query_faq" for item in prepared.tool_results)
            if direct:
                yield ToolEvent(name="query_faq", args={"keyword": message}, phase="start")
            result = await _answer_knowledge(session_factory, prepared)
            if direct:
                yield tool_event_from(prepared.tool_results[-1], phase="end")
            yield SourcesEvent(result.sources, result.refused, result.low_confidence_question_id)
            yield TokenEvent(text=result.answer)
            persist_turn(
                session_factory,
                prepared,
                answer=result.answer,
                citations=[source.model_dump() for source in result.sources],
            )
            yield DoneEvent()
            return

        if prepared.ai.tool_calls:
            # 收敛:这一次**不 bind_tools**。模型没有工具可调,收敛不是靠嘱咐,
            # 是靠它调不到 —— 这是"只做单轮"的结构性保证。
            convergence_messages = [*prepared.messages, prepared.ai, *prepared.tool_messages]
            collected: AIMessageChunk | None = None
            async for chunk in get_chat_model().astream(convergence_messages, **model_kwargs()):
                if chunk.text:
                    yield TokenEvent(text=chunk.text)
                collected = chunk if collected is None else collected + chunk
            answer = collected.text if collected is not None else ""
        else:
            # 没调工具:turn1 的正文**就是**最终答案,而它已经在上面逐块吐过了。
            # 这里只取拼接结果去落库与判空 —— 再吐一遍会变成重复正文。
            answer = prepared.ai.content if isinstance(prepared.ai.content, str) else ""
            if prepared.query is not None and prepared.query.route == "business" and answer:
                yield TokenEvent(text=answer)

        if not answer.strip():
            # 复用 ch01 的异常类:空回答会作为一条真正的空 assistant 消息永久重放。
            # 注意判空的**只有这一步** —— turn1 的空正文是合法的:上游带 tool_calls
            # 时常常同时给一段前言(实测 "I'll look up the logistics information
            # for order 1001."),但**空**也合法 —— 工具调用轮本来就可以没有正文。
            raise EmptyCompletionError("模型没有产出任何内容,本轮按失败处理")

        persist_turn(session_factory, prepared, answer=answer)
        yield DoneEvent()


@dataclass(frozen=True)
class AgentTurnResult:
    """一轮的完整结果 —— eval、脚本与单测的唯一数据源。"""

    session_id: str
    conversation_id: int
    resumed: bool
    answer: str
    tool_calls: list[dict]
    tool_results: list[ToolResult]
    sources: list[SourceDTO] = field(default_factory=list)
    refused: bool = False
    low_confidence_question_id: str | None = None


@trace_legacy('ch02_agent')
async def run_agent_turn(
    session_factory: SessionFactory,
    *,
    session_id: str | None,
    user_id: str,
    message: str,
    filters: SearchFilters | None = None,
    tool_runtime=None,
) -> AgentTurnResult:
    """跑一轮,一次性返回完整轨迹 + 答案。

    与 `stream_agent_turn` 共用 `_prepare_turn`,只在收敛那步不同:
    这里用 `ainvoke` 拿完整文本,不逐 token 吐。存在的理由是**可观测** ——
    `curl /ch02/agent` 一眼看到模型选了哪个工具,评估集也省掉解 SSE 的活。

    生成 id 与加锁的理由与流式出口**逐条相同**(那个出口的 docstring 里有完整说明)
    —— 两个出口必须成对地做这两件事,少一个就有一种调用方式不受保护。
    """
    resolved = session_id or uuid4().hex

    async with store.lock(resolved):
        prepared = await _prepare_turn(
            session_factory, session_id=resolved, user_id=user_id, message=message, filters=filters,
            tool_runtime=tool_runtime
        )

        if _needs_knowledge(prepared):
            result = await _answer_knowledge(session_factory, prepared)
            persist_turn(
                session_factory,
                prepared,
                answer=result.answer,
                citations=[source.model_dump() for source in result.sources],
            )
            return AgentTurnResult(
                prepared.session_id,
                prepared.conversation_id,
                prepared.resumed,
                result.answer,
                list(prepared.ai.tool_calls),
                prepared.tool_results,
                result.sources,
                result.refused,
                result.low_confidence_question_id,
            )
        if prepared.ai.tool_calls:
            # 收敛:同样**不 bind_tools**。单轮是两个出口共同的硬约束。
            convergence_messages = [*prepared.messages, prepared.ai, *prepared.tool_messages]
            ai = await get_chat_model().ainvoke(convergence_messages, **model_kwargs())
            answer = ai.content if isinstance(ai.content, str) else str(ai.content)
        else:
            answer = prepared.ai.content if isinstance(prepared.ai.content, str) else ""

        if not answer.strip():
            raise EmptyCompletionError("模型没有产出任何内容,本轮按失败处理")

        persist_turn(session_factory, prepared, answer=answer)

        # return 在 `async with` **里面** —— 挪到外面就是锁外返回,而那正是
        # "两个出口必须成对做这两件事"里最容易漏掉的一半。
        return AgentTurnResult(
            session_id=prepared.session_id,
            conversation_id=prepared.conversation_id,
            resumed=prepared.resumed,
            answer=answer,
            tool_calls=list(prepared.ai.tool_calls),
            tool_results=list(prepared.tool_results),
        )
