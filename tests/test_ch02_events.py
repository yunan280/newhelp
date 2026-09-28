"""SSE 事件对象 —— service 层产出对象,不产出 `data:` 帧。

这是 ch01 定下的边界(spec §5):传输格式归 api 层管。事件对象能脱离 HTTP 单测,
`data:` 帧不能。
"""

from mewhelp.ch02.events import (
    AgentEvent,
    DoneEvent,
    SessionEvent,
    TokenEvent,
    ToolEvent,
    tool_event_from,
)
from mewhelp.tools.infra import ToolResult


def test_session_event_carries_the_resumed_flag():
    """resumed 把不可见的上下文丢失变可见(spec §12.3)。

    没有它的话,"带了个本地已过期的 session_id"与"接着聊"在客户端看来一模一样 ——
    用户以为在续接,其实是新对话。
    """
    assert SessionEvent(session_id="s1", resumed=False).resumed is False
    assert SessionEvent(session_id="s1", resumed=True).resumed is True


def test_tool_event_start_has_no_outcome_fields():
    """phase=start 时结果还没产生,三个结果字段必须停在 None。

    前端靠 phase 判断画什么:start 挂徽章,end 补耗时。若 start 就带上
    `ok=False` 这样的默认值,前端会先把徽章画成"失败"再改回来。
    """
    event = ToolEvent(name="query_logistics", args={"order_id": "1001"}, phase="start")
    assert event.ok is None
    assert event.elapsed_ms is None
    assert event.attempts is None


def test_tool_event_end_fills_the_outcome():
    event = tool_event_from(
        ToolResult(name="query_logistics", args={"order_id": "1001"}, ok=True,
                   content="已到杭州", error=None, elapsed_ms=12, attempts=1),
        phase="end",
    )
    assert (event.phase, event.ok, event.elapsed_ms, event.attempts) == ("end", True, 12, 1)
    assert event.name == "query_logistics"
    assert event.args == {"order_id": "1001"}


def test_tool_event_end_reports_failure():
    event = tool_event_from(
        ToolResult(name="query_faq", args={"keyword": "邮费"}, ok=False,
                   content="没有找到", error="invalid_args", elapsed_ms=0, attempts=0),
        phase="end",
    )
    assert event.ok is False
    assert event.attempts == 0


def test_token_and_done_are_thin():
    assert TokenEvent(text="您好").text == "您好"
    # ch01 的如实声明继续有效:这个字段当前恒为 "stop",别拿它判截断。
    assert DoneEvent().finish_reason == "stop"


def test_all_four_are_members_of_the_union():
    for event in (
        SessionEvent(session_id="s", resumed=False),
        ToolEvent(name="t", args={}, phase="start"),
        TokenEvent(text="x"),
        DoneEvent(),
    ):
        assert isinstance(event, AgentEvent.__args__)  # type: ignore[attr-defined]
