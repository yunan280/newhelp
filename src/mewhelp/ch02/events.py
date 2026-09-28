"""编排层产出的事件对象。

**这一层不产出 `data:` 帧** —— 那是 api 层的事(ch01 定下的边界)。
分开的好处是事件流能脱离 HTTP 单元测试:断言 `events[0] == SessionEvent(...)`
比断言一串 SSE 文本精确得多。

`tool` 事件带 phase 而不是只发一次:工具执行可能是秒级的,先发 start 让前端
立刻挂上徽章,执行完再补 ok/耗时。用户因此知道"它在查"而不是"它卡住了"。
"""

from dataclasses import dataclass
from typing import Literal, Union

from mewhelp.tools.infra import ToolResult


@dataclass(frozen=True)
class SessionEvent:
    """永远是第一个事件。"""

    session_id: str
    # 客户端本地已有历史、但服务端没找到这个 session_id 时为 False ——
    # 前端据此提示"这是一段新对话",而不是让用户以为在续接。
    resumed: bool


@dataclass(frozen=True)
class ToolEvent:
    name: str
    args: dict
    phase: Literal["start", "end"]
    ok: bool | None = None
    elapsed_ms: int | None = None
    attempts: int | None = None


@dataclass(frozen=True)
class TokenEvent:
    text: str


@dataclass(frozen=True)
class DoneEvent:
    # 沿用 ch01:这个字段当前恒为 "stop",**不代表**上游真实的截断状态。
    # 契约里这个字段承诺得比实现多,所以在这里如实写明,而不是让客户端去猜。
    finish_reason: str = "stop"


AgentEvent = Union[SessionEvent, ToolEvent, TokenEvent, DoneEvent]


def tool_event_from(result: ToolResult, *, phase: Literal["start", "end"]) -> ToolEvent:
    return ToolEvent(
        name=result.name,
        args=result.args,
        phase=phase,
        ok=result.ok,
        elapsed_ms=result.elapsed_ms,
        attempts=result.attempts,
    )
