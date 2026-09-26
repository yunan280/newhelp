"""会话历史 —— 进程内存储 + token 预算裁剪。"""

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
