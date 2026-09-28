"""第 2 章编排 —— 单轮工具调用。

两个出口共用一个核心:
- `stream_agent_turn` 逐 token 流式(SSE 用)
- `run_agent_turn` 一次性返回(JSON / eval / 测试用)
差别只在**收敛那一步**,前六步完全一样,所以抽成 `_prepare_turn`。

`model.astream(...)` 这一层只产出事件对象,不关心 SSE 的帧格式 ——
传输格式归 api 层(ch01 定下的边界)。
"""

import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
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
from mewhelp.config import HISTORY_TOKEN_BUDGET
from mewhelp.db.repository import (
    TurnMessage,
    append_messages,
    get_or_create_conversation,
    load_replay_messages,
)
from mewhelp.llm import get_chat_model
from mewhelp.memory import store, trim_history
from mewhelp.tools.infra import ToolResult
from mewhelp.tools.registry import ToolRegistry
from mewhelp.tools.ticket import build_registry

from .events import AgentEvent, DoneEvent, SessionEvent, TokenEvent, ToolEvent, tool_event_from
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
    messages: list[BaseMessage]      # 送给模型的历史 + 本轮提问(含 system)
    ai: AIMessage                    # turn1 的完整返回(可能带 tool_calls)
    registry: ToolRegistry
    tool_results: list[ToolResult]

    @property
    def tool_messages(self) -> list[ToolMessage]:
        """把工具结果翻成回灌用的 ToolMessage,tool_call_id 与申请单对号入座。"""
        return [
            ToolMessage(content=result.content, tool_call_id=call["id"])
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
) -> AsyncIterator[AgentEvent]:
    """turn1 的**事件版**:会话身份 → 组装上下文 → 定工具 → 执行工具。

    事件在这一层产出、而不是等 `_prepare_turn` 整个跑完再补,是因为顺序本身就是
    spec §11 ③④ 的要求:

    - `session` 排在 turn1 之前 —— 它是身份,不是结果;
    - turn1 的正文**边到边吐**(方案 (c))。模型既说前言又调工具时,前言与最终答案
      同框,这是 (c) 相对 (a)(b) 的全部收益;把它们攒到最后再吐,收益就没了。
      不调工具的轮次更直接:turn1 的正文**就是**最终答案,攒起来等于把 ch01 的
      打字机丢掉 —— 而那才是多数轮次。
    - `tool` 的 start 帧在**执行之前**推。徽章的意义是"它在查,不是卡住了";
      等执行完再发,徽章挂出来时查询已经结束,这一帧只剩事后记录的价值。
      所以 start 帧只有 name/args,结果字段留 None(等 end 帧补)。

    不落库、不加锁、不生成 id —— 都归两个出口。理由见下面 `_prepare_turn` 的说明。

    **写 `sink.prepared` 之后才结束**;调用方遍历完这个生成器才能拿到它。
    """
    with session_factory() as session:
        conv, created = _resolve_conversation(
            session, session_id=session_id, user_id=user_id
        )
        conversation_id = conv.id
        resumed = not created
        session.commit()

        history_rows = load_replay_messages(session, conversation_id=conversation_id)
        history = trim_history(_to_messages(history_rows), max_tokens=HISTORY_TOKEN_BUDGET)

    yield SessionEvent(session_id=session_id, resumed=resumed)

    messages: list[BaseMessage] = [
        SystemMessage(content=AGENT_SYSTEM),
        *history,
        HumanMessage(content=message),
    ]

    registry = build_registry(session_factory, conversation_id)

    # turn1 走 astream 而不是 ainvoke:模型既可能只吐工具调用,也可能先说一句
    # 前言再调工具。若走 ainvoke,"不需要工具"的那些轮次就再也流不了式了 ——
    # 而那是**多数**轮次,ch01 的流式会白做(spec §11 的方案 (c))。
    # 分片的 tool_call_chunks 靠 AIMessageChunk 相加拼回完整 tool_calls(已实测)。
    collected: AIMessageChunk | None = None
    async for chunk in get_chat_model().bind_tools(registry.tools()).astream(messages):
        if chunk.text:
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
        results = await registry.run_all(ai.tool_calls)

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
    )


async def _prepare_turn(
    session_factory: SessionFactory,
    *,
    session_id: str,
    user_id: str,
    message: str,
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
        session_factory, session_id=session_id, user_id=user_id, message=message, sink=sink
    ):
        pass
    if sink.prepared is None:  # pragma: no cover —— 生成器必然在耗尽前写入
        raise RuntimeError("事件流结束了却没有产出 PreparedTurn")
    return sink.prepared


def build_turn_rows(prepared: PreparedTurn, *, answer: str) -> list[TurnMessage]:
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
    rows.append(TurnMessage(role=MsgRole.assistant, content=answer))
    return rows


def persist_turn(
    session_factory: SessionFactory, prepared: PreparedTurn, *, answer: str
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
                rows=build_turn_rows(prepared, answer=answer),
            )
            session.commit()
    except Exception:  # 落库失败不许推翻已经答完的那一轮
        logging.getLogger(__name__).exception(
            "落库失败,本轮账本缺行(session_id=%s)", prepared.session_id
        )


async def stream_agent_turn(
    session_factory: SessionFactory,
    *,
    session_id: str | None,
    user_id: str,
    message: str,
) -> AsyncIterator[AgentEvent]:
    """跑一轮,逐 token 产出事件。

    事件顺序:session → tool(start/end)* → token* → done。
    上游挂了、或最终回答为空,异常**直接抛出去** —— api 层翻成 error 帧,
    这一层不产出 error 事件(它只管"发生了什么",不管传输格式)。

    无工具的那条路也走流式:turn1 的正文就是最终答案,逐 token 吐出去。
    这是方案 (c) 换来的东西 —— 非流式方案会让**多数**轮次丢掉打字机。

    **锁罩住整轮**(读历史 → 定工具 → 执行 → 收敛 → 落库),不只是前半段。
    理由是"读历史"与"落库"必须成对原子:只锁前半段的话,同一 session 的两轮
    会各自读到同一份历史、各自收敛、再各写各的,落库顺序交错 —— 而两轮都以为
    自己接住了上下文。用户双击发送就能撞上。

    生成的 id 在锁**之前**算出来:锁是按 id 取的,没有 id 就无从加锁。
    这就是"生成 id 归出口"的全部原因。
    """
    resolved = session_id or uuid4().hex

    async with store.lock(resolved):
        sink = _Sink()
        # turn1 的事件**直接转发**,不在这一层缓冲:typing 效果就靠它。
        async for event in _prepare_turn_events(
            session_factory, session_id=resolved, user_id=user_id, message=message, sink=sink
        ):
            yield event

        prepared = sink.prepared
        if prepared is None:  # pragma: no cover —— 生成器必然在耗尽前写入
            raise RuntimeError("事件流结束了却没有产出 PreparedTurn")

        if prepared.ai.tool_calls:
            # 收敛:这一次**不 bind_tools**。模型没有工具可调,收敛不是靠嘱咐,
            # 是靠它调不到 —— 这是"只做单轮"的结构性保证。
            convergence_messages = [*prepared.messages, prepared.ai, *prepared.tool_messages]
            collected: AIMessageChunk | None = None
            async for chunk in get_chat_model().astream(convergence_messages):
                if chunk.text:
                    yield TokenEvent(text=chunk.text)
                collected = chunk if collected is None else collected + chunk
            answer = collected.text if collected is not None else ""
        else:
            # 没调工具:turn1 的正文**就是**最终答案,而它已经在上面逐块吐过了。
            # 这里只取拼接结果去落库与判空 —— 再吐一遍会变成重复正文。
            answer = prepared.ai.content if isinstance(prepared.ai.content, str) else ""

        if not answer.strip():
            # 复用 ch01 的异常类:空回答会作为一条真正的空 assistant 消息永久重放。
            # 注意判空的**只有这一步** —— turn1 的空正文是合法的:上游带 tool_calls
            # 时常常同时给一段前言(实测 "I'll look up the logistics information
            # for order 1001."),但**空**也合法 —— 工具调用轮本来就可以没有正文。
            raise EmptyCompletionError("模型没有产出任何内容,本轮按失败处理")

        persist_turn(session_factory, prepared, answer=answer)
        yield DoneEvent()
