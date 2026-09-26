"""第 1 章的 HTTP 接口。

用 FastAPI 原生 SSE(fastapi.sse),不手写 data:...\\n\\n 的拼接。

事件契约(客户端按这个来写,别去猜):
- `session`:永远是第一个事件,带 `session_id`。客户端存下来,下一轮原样传回。
- `token`:零到多个,带 `text`(一段文本,不是增量语义上的字符)。拼接即完整回复。
- `done`:成功收尾,带 `finish_reason`。
- `error`:失败收尾,带 `message`(人类可读)与 `code`(机器可判,见下)。

关于 `error` —— 这是客户端最需要知道的一条:
**这一轮没有写进会话历史**,用户这一轮的提问服务端不再保留。之前各轮完好无损
(存储里是未裁剪的原始历史,被丢掉的只是本轮的 human + ai 那一对)。
所以重试的方式就是**把同一条消息原样再发一次**,不需要补发更早的对话,
也不会重复计一轮。

`code` 取值:
- `"empty_completion"`:模型没产出任何内容(或只有空白)。上游是通的,
  是这一轮的输出不合格 —— 重试有意义。
- `"upstream_error"`:其余一切上游异常(网络断、5xx、超时)。断在哪一步都可能,
  本轮已推给客户端的 token 依旧作数,但服务端不留痕。

两种情况都**不是**换一条 session_id 能解决的,客户端别因为一个 error 就丢掉会话。
"""

from collections.abc import AsyncIterable
from uuid import uuid4

from fastapi import APIRouter
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, Field, field_validator

from .service import EmptyCompletionError, stream_chat

router = APIRouter(prefix="/ch01", tags=["ch01"])


class ChatRequest(BaseModel):
    session_id: str | None = Field(
        default=None,
        description="会话 id。不传则服务端生成,并从 session 事件返回。",
    )
    message: str = Field(min_length=1, description="用户这一轮说的话,不能为空。")

    @field_validator("session_id")
    @classmethod
    def _reject_blank_session_id(cls, value: str | None) -> str | None:
        """传了空白的会话 id 要拒,不能当成"没传"。

        `req.session_id or uuid4().hex` 会把 `""` 吞掉,于是**每一轮都开一个新会话** ——
        客户端拿回一个看着完全正常的新 id,上下文却整段丢失,全程没有任何报错。
        JS 里 `""` 是 falsy、变量未赋值读作空串,踩中的成本极低(本章没有客户端,
        但 Task 11 的聊天页就是 JS 写的),所以这里显式拦掉。

        id 是不透明串:非空白的原样放行,**不 strip** —— 服务端不该改写客户端给的标识。
        """
        if value is not None and not value.strip():
            raise ValueError("session_id 不能只有空白字符")
        return value

    @field_validator("message")
    @classmethod
    def _reject_blank_message(cls, value: str) -> str:
        """`min_length=1` 只拦得住空串,拦不住 `"   "`。

        纯空白既不是内容,又会照常花掉一次真实的上游调用 —— 与空串同害。
        这里只校验,不 strip:消息文本原样往下传,由模型去理解首尾空白。
        """
        if not value.strip():
            raise ValueError("message 不能只有空白字符")
        return value


@router.post("/chat/stream", response_class=EventSourceResponse)
async def chat_stream(req: ChatRequest) -> AsyncIterable[ServerSentEvent]:
    """流式对话。事件顺序:session → token* → done;出错则是 session → token* → error。"""
    session_id = req.session_id or uuid4().hex

    # 无论客户端有没有传,session 事件总是第一个 —— 客户端据此确认续接用的 id
    yield ServerSentEvent(event="session", data={"session_id": session_id})

    try:
        async for piece in stream_chat(session_id, req.message):
            yield ServerSentEvent(event="token", data={"text": piece})
    except EmptyCompletionError as exc:
        # 空回复单独给一个 code:客户端据此判断"重试有意义"还是"上游出事了"
        yield ServerSentEvent(event="error", data={"message": str(exc), "code": "empty_completion"})
        return
    except Exception as exc:  # noqa: BLE001 —— 任何上游异常都要转成 error 事件
        yield ServerSentEvent(event="error", data={"message": str(exc), "code": "upstream_error"})
        return

    yield ServerSentEvent(event="done", data={"finish_reason": "stop"})
