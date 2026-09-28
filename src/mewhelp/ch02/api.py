"""第 2 章的 HTTP 接口。

两条路由共用一个编排核心:
- `POST /ch02/chat/stream` —— SSE,前端主入口
- `POST /ch02/agent`       —— JSON,程序化 / eval / curl 出口

**这一层负责成帧,service 层只产出事件对象**(ch01 定下的边界)。

事件契约(客户端按这个来写,别去猜):在 ch01 的 `session` / `token` / `done` /
`error` 之上**新增一个 `tool`**。老页面不认识它,会直接忽略,不会坏。

`session` 仍是第一个事件,payload 多了 `resumed`:客户端本地有历史、而服务端
没找到这个 session_id 时为 false —— 前端据此提示"这是一段新对话",而不是让用户
以为在续接。这是本设计里唯一一处把不可见的上下文丢失变可见的地方。

`done` 的 `finish_reason` 与 ch01 一样恒为 `"stop"`,**不代表上游真实的截断状态**。
"""

from collections.abc import AsyncIterable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy.orm import Session

from mewhelp.ch01.service import EmptyCompletionError

from .events import SessionEvent, TokenEvent, ToolEvent
from .schemas import AgentRequest, ChatRequest
from .service import run_agent_turn, stream_agent_turn

router = APIRouter(prefix="/ch02", tags=["ch02"])


def get_session_factory() -> Callable[[], Session]:
    """FastAPI 依赖,返回一个 session 工厂。

    为什么不直接 `Depends(get_session)` 给一个 Session:工具经 @tool 的 ainvoke
    跑在**线程池**里(实测),而 SQLAlchemy 的 Session 非线程安全。编排层要的是
    "能开新 Session 的东西",不是"一个 Session"。

    做成依赖而不是模块级常量,是为了测试能用 `dependency_overrides` 换成内存库。
    """
    from mewhelp.db.engine import SessionLocal

    return SessionLocal


# `Depends(...)` 写成**默认值**会被 ruff 的 B008 拦下(FastAPI 的经典误报)。
# 用 `Annotated` 挂依赖是它的正解,顺带也不用在这一层挂抑制指令。
SessionFactoryDep = Annotated[Callable[[], Session], Depends(get_session_factory)]


def _frame(event) -> ServerSentEvent:
    """事件对象 → SSE 帧。成帧只在这一处。

    `tool` 帧在 phase="end" 时才带结果字段:start 时带着 `ok=None` 发出去,
    前端多半会把它渲染成"这一轮失败了" —— 而那一刻工具还在跑。
    """
    if isinstance(event, SessionEvent):
        return ServerSentEvent(
            event="session", data={"session_id": event.session_id, "resumed": event.resumed}
        )
    if isinstance(event, ToolEvent):
        data: dict = {"name": event.name, "args": event.args, "phase": event.phase}
        if event.phase == "end":
            data.update(ok=event.ok, elapsed_ms=event.elapsed_ms, attempts=event.attempts)
        return ServerSentEvent(event="tool", data=data)
    if isinstance(event, TokenEvent):
        return ServerSentEvent(event="token", data={"text": event.text})
    return ServerSentEvent(event="done", data={"finish_reason": event.finish_reason})


@router.post("/chat/stream", response_class=EventSourceResponse)
async def chat_stream(
    req: ChatRequest,
    session_factory: SessionFactoryDep,
) -> AsyncIterable[ServerSentEvent]:
    """流式对话(带工具链)。

    事件顺序:session → tool* → token* → done;出错则是 …→ error。
    流已经以 200 开始了,错误只能走 `error` 帧 —— 不静默断流。
    """
    try:
        async for event in stream_agent_turn(
            session_factory,
            session_id=req.session_id,
            user_id=req.resolved_user_id,
            message=req.message,
        ):
            yield _frame(event)
    except EmptyCompletionError as exc:
        yield ServerSentEvent(
            event="error", data={"message": str(exc), "code": "empty_completion"}
        )
    except Exception as exc:  # noqa: BLE001 —— 任何上游异常都要转成 error 事件
        yield ServerSentEvent(
            event="error", data={"message": str(exc), "code": "upstream_error"}
        )


@router.post("/agent")
async def agent(
    req: AgentRequest,
    session_factory: SessionFactoryDep,
) -> dict:
    """非流式出口 —— 一次性返回完整工具轨迹 + 答案。

    存在的理由是**可观测**:`curl` 一眼看到模型选了哪个工具,评估集也不必解析 SSE。
    与 /chat/stream 共用编排核心,不重复实现。

    失败一律 502:**上游出错**与**模型没产出内容**对调用方是同一件事 —— 这一轮
    没拿到结果,重试有意义。两者用 detail 的文字区分,不用状态码区分,因为调用方
    能做的事完全一样。
    """
    try:
        result = await run_agent_turn(
            session_factory,
            session_id=req.session_id,
            user_id=req.resolved_user_id,
            message=req.message,
        )
    except EmptyCompletionError as exc:
        raise HTTPException(status_code=502, detail=f"模型没有产出任何内容:{exc}") from exc
    # 这里不加抑制指令 BLE001:那条是给"吞掉异常"的 except 用的,本处是 re-raise,
    # 规则本就不触发 —— 挂一个用不上的指令只会被 RUF100 反过来抓出来。
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"上游出错:{exc}") from exc

    return {
        "session_id": result.session_id,
        "conversation_id": result.conversation_id,
        "resumed": result.resumed,
        "answer": result.answer,
        "tool_calls": result.tool_calls,
        "tool_results": [
            {
                "name": r.name,
                "ok": r.ok,
                "content": r.content,
                "error": r.error,
                "elapsed_ms": r.elapsed_ms,
                "attempts": r.attempts,
            }
            for r in result.tool_results
        ],
    }
