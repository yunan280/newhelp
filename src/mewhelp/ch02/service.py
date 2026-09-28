"""第 2 章编排 —— 单轮工具调用。

两个出口共用一个核心:
- `stream_agent_turn` 逐 token 流式(SSE 用)
- `run_agent_turn` 一次性返回(JSON / eval / 测试用)
差别只在**收敛那一步**,前六步完全一样,所以抽成 `_prepare_turn`。

`model.astream(...)` 这一层只产出事件对象,不关心 SSE 的帧格式 ——
传输格式归 api 层(ch01 定下的边界)。
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

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

from mewhelp.config import HISTORY_TOKEN_BUDGET
from mewhelp.db.repository import (
    TurnMessage,
    append_messages,
    get_or_create_conversation,
    load_replay_messages,
)
from mewhelp.llm import get_chat_model
from mewhelp.memory import trim_history
from mewhelp.tools.infra import ToolResult
from mewhelp.tools.registry import ToolRegistry
from mewhelp.tools.ticket import build_registry

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
        collected = chunk if collected is None else collected + chunk

    ai = AIMessage(
        content=collected.text if collected is not None else "",
        tool_calls=collected.tool_calls if collected is not None else [],
    )

    # 用 asyncio.gather 并发跑:一轮里多个工具是相互独立的读,没有先后依赖。
    # 结果顺序与 tool_calls 一致,靠的是 run_all 内部的 gather 保序。
    results = await registry.run_all(ai.tool_calls) if ai.tool_calls else []

    return PreparedTurn(
        session_id=session_id,
        conversation_id=conversation_id,
        resumed=resumed,
        messages=messages,
        ai=ai,
        registry=registry,
        tool_results=results,
    )


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
