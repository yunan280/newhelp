"""第 1 章业务编排 —— 纯对话。

这一层只产出文本片段,不关心 SSE 的封装格式;传输格式归 api 层管。
"""

from collections.abc import AsyncIterator

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from mewhelp.config import HISTORY_TOKEN_BUDGET
from mewhelp.llm import get_chat_model, get_structured_model
from mewhelp.memory import store, trim_history

from .prompts import CHAT_PROMPT, EXTRACT_PROMPT
from .schemas import AfterSalesTicket


class EmptyCompletionError(RuntimeError):
    """模型没有产出任何内容 —— 判定为空回复的失败回合。

    特意做成 RuntimeError 的子类:调用方想区分"空回复"与"上游断流",
    就看这个类型;不关心的调用方 `except RuntimeError` 照旧兜得住。
    两者共用一套异常,靠正则匹配 message 文案来分辨,是这条子类要消灭的东西。
    """


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

        reply_text = collected.text if collected is not None else ""
        if not reply_text.strip():
            # 空回复按失败处理,不写回历史。写进去就是一条真正的空 assistant 消息,
            # 之后每一轮都会被重放进上下文;而客户端只看到"这轮一个 token 都没有",
            # 误以为是成功的空回答。交 api 层转成 SSE error 事件。
            #
            # 判据看的是 strip 之后的文本:只有空白字符的回复(几个空格、一个换行)
            # 与真正的空回复同害,但它不是 falsy,`if not reply_text` 会放它过去。
            # 存进历史的仍是**未 strip** 的原文,别把回复里有意义的首尾空白吃掉。
            raise EmptyCompletionError("模型没有产出任何内容,本轮按失败处理")

        await store.append(session_id, human, AIMessage(content=reply_text))


async def extract_ticket(description: str) -> AfterSalesTicket | None:
    """从一段售后描述里抽取工单要素。

    模型没能给出结构化结果时返回 None —— 由 api 层转成 422。
    这里不重试:重试策略等评估跑出数据再定。
    """
    messages = EXTRACT_PROMPT.format_messages(description=description)
    return await get_structured_model(AfterSalesTicket).ainvoke(messages)
