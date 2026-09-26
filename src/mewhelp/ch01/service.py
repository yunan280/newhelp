"""第 1 章业务编排 —— 纯对话。

这一层只产出文本片段,不关心 SSE 的封装格式;传输格式归 api 层管。
"""

from collections.abc import AsyncIterator

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from mewhelp.config import HISTORY_TOKEN_BUDGET
from mewhelp.llm import get_chat_model
from mewhelp.memory import store, trim_history

from .prompts import CHAT_PROMPT


async def stream_chat(session_id: str, message: str) -> AsyncIterator[str]:
    """跑一轮对话,逐段产出回复文本。

    整轮被会话锁包住:同一 session 的并发请求会排队,不会两轮读到同一份旧历史。
    代价是一轮没跑完,同会话的下一个请求要等 —— 对聊天场景这是想要的行为。
    """
    async with store.lock(session_id):
        history = await store.get(session_id)
        trimmed = trim_history(history, max_tokens=HISTORY_TOKEN_BUDGET)

        human = HumanMessage(content=message)
        # CHAT_PROMPT 已经带上了 System Prompt,history 只放对话消息
        messages = CHAT_PROMPT.format_messages(history=trimmed) + [human]

        collected: AIMessageChunk | None = None
        async for chunk in get_chat_model().astream(messages):
            if chunk.text:
                yield chunk.text
            collected = chunk if collected is None else collected + chunk

        reply = AIMessage(content=collected.text if collected is not None else "")
        await store.append(session_id, human, reply)
