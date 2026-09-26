"""会话历史 —— 进程内存储 + token 预算裁剪。"""

import asyncio

from langchain_core.messages import BaseMessage
from langchain_core.messages.utils import (
    count_tokens_approximately,
    trim_messages,
)


def trim_history(
    history: list[BaseMessage],
    *,
    max_tokens: int,
    token_counter=count_tokens_approximately,
) -> list[BaseMessage]:
    """把历史裁到 token 预算内,超出部分从最老的开始丢。

    两个关键参数:
    - strategy="last":从最新往回保留,丢掉的是早期内容
    - start_on="human":裁完必须落在 human 消息上,否则历史会以一条 AI 回复
      开头 —— 模型看到"自己刚说过话"却没有对应提问,容易答非所问

    count_tokens_approximately 是字符数启发式,中文会低估。本章预算 2048
    远小于 DeepSeek 的 128K 窗口,低估不会溢出。要更准就把 token_counter
    换成 tiktoken 或自造的 CJK 计数器 —— 这个参数就是留给那时的口子。
    """
    if not history:
        return []
    return trim_messages(
        history,
        strategy="last",
        token_counter=token_counter,
        max_tokens=max_tokens,
        start_on="human",
    )


class SessionStore:
    """进程内会话存储。

    本章够用:单 worker 运行,进程重启会话即丢。后续换成 MySQL / Redis
    只需要替换这个类,调用方(service 层)不用动 —— 所以这几个方法
    现在就是 async 的,哪怕它们还不做 IO。

    已知局限:没有上限和淘汰,会话数一直涨会持续吃内存。
    """

    def __init__(self) -> None:
        self._sessions: dict[str, list[BaseMessage]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, session_id: str) -> asyncio.Lock:
        """拿到该会话的锁。

        同一会话的并发请求必须串行:否则两个请求会读到同一份旧历史,
        各自追加一轮,第二轮丢失可见性。service 层用一整轮对话包住这把锁。
        """
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    async def get(self, session_id: str) -> list[BaseMessage]:
        """返回**副本**,调用方改它不会污染内部状态。"""
        return list(self._sessions.get(session_id, []))

    async def append(self, session_id: str, *messages: BaseMessage) -> None:
        self._sessions.setdefault(session_id, []).extend(messages)

    async def clear(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


# 进程级单例。ch01 的 service 直接用这个。
store = SessionStore()
