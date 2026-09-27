"""第 1 章的 HTTP 接口。

用 FastAPI 原生 SSE(fastapi.sse),不手写 data:...\\n\\n 的拼接。

事件契约(客户端按这个来写,别去猜):
- `session`:永远是第一个事件,带 `session_id`。客户端存下来,下一轮原样传回。
- `token`:零到多个,带 `text`(一段文本,不是增量语义上的字符)。拼接即完整回复。
- `done`:成功收尾,带 `finish_reason`。**这个字段当前恒为 `"stop"`(硬编码的字面量),
  不代表上游真实的截断状态 —— 别拿它判断"答完了"还是"被截断了"。** 上游因长度限制
  截断答复时,客户端收到的仍是 `"stop"`;契约里这个字段承诺得比实现多,所以在这里
  如实写明当前值,而不是让客户端去猜。要拿真值,得从 `collected.response_metadata`
  取(那是 service 返回契约的一次改动),本章不做。
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

以上都是 `/chat/stream` 的契约。同模块的 `/extract` 是另一类接口:普通 JSON、无状态、
不流式 —— 失败时是 422 加一个 `detail` 字符串,没有 `error` 事件这回事。
"""

from collections.abc import AsyncIterable
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.sse import EventSourceResponse, ServerSentEvent
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schemas import AfterSalesTicket
from .service import EmptyCompletionError, extract_ticket, stream_chat

router = APIRouter(prefix="/ch01", tags=["ch01"])

# 422 的 detail 里最多嵌多少个字符的模型原始输出:detail 是发给客户端的,
# 而坏掉的模型可能吐出一大段。超长就截断,并**明说**截断了。
_RAW_TEXT_LIMIT = 500

# 「空白」这一类的第二个码位族。`str.strip()` 按 `str.isspace()` 语义工作,而
# U+200B 这类零宽字符**不在其中** —— 只判 `strip()` 的话,一个由零宽空格拼成的
# message 会照常花掉一次真实的上游调用,与空串同害,只是肉眼看不见。
# 顺序:零宽空格 / 零宽非连接符 / 零宽连接符 / 零宽不换行空格(BOM)。
_ZERO_WIDTH_CHARS = "\u200b\u200c\u200d\ufeff"
_ZERO_WIDTH_TABLE = str.maketrans("", "", _ZERO_WIDTH_CHARS)


def _reject_blank(value: str | None, field: str) -> str | None:
    """空串、纯空白、纯零宽字符都拒掉,其余原样返回。

    `min_length=1` 只拦得住空串。纯空白长度够、却不是内容,而且照常花掉一次真实
    的上游调用 —— 与空串同害。零宽字符是同一件事的另一个码位:`str.strip()` 按
    `str.isspace()` 语义工作,U+200B / U+200C / U+200D / U+FEFF 都不在其中,于是
    `{"message": "\u200b"}` 能一路走到上游。复制粘贴带零宽字符的文本成本极低,
    所以这里把两类一起去掉之后再判空。`field` 只用来拼错误文案(422 的 detail
    要指明是哪个字段)。

    只校验,不 strip:文本原样往下传,由模型去理解首尾空白。把返回值改成 `.strip()`
    会把用户真正说的那句话改掉 —— 拒绝能力一点没丢,改掉的是被回答的那句话本身。
    零宽字符同理:只在**判定**时去掉,返回值里一个字节都不动。

    **None 是"这个字段没传",不是空白**,原样放行:`session_id` 是 Optional,
    这里若直接 `value.strip()` 会 AttributeError(落到客户端是 500 而不是 422)。
    """
    if value is None:
        return None
    if not value.translate(_ZERO_WIDTH_TABLE).strip():
        raise ValueError(f"{field} 不能只有空白或零宽字符")
    return value


def _embed_raw(raw: str) -> str:
    """把模型原始输出嵌进 422 的 detail —— 超长截断,并说清截断了。"""
    if len(raw) <= _RAW_TEXT_LIMIT:
        return raw
    return f"{raw[:_RAW_TEXT_LIMIT]}…(已截断,原始输出共 {len(raw)} 字符)"


class ChatRequest(BaseModel):
    # 端点不认的字段必须 422,不能静默丢掉:客户端把 `session_id` 拼错成别的名字时,
    # 看着一切正常,实际每一轮都在开新会话。
    model_config = ConfigDict(extra="forbid")

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
        判定交给 `_reject_blank`,和 `message` / `description` 共用同一条规则;
        "没传"(None)由它原样放行。
        """
        return _reject_blank(value, "session_id")

    @field_validator("message")
    @classmethod
    def _reject_blank_message(cls, value: str) -> str:
        """`min_length=1` 只拦得住空串,拦不住 `"   "`。

        纯空白既不是内容,又会照常花掉一次真实的上游调用 —— 与空串同害。
        规则本体在 `_reject_blank`,和 `session_id` / `description` 共用,不在这里复制一份。
        """
        return _reject_blank(value, "message")


class ExtractRequest(BaseModel):
    # 同 ChatRequest:多传的字段直接 422。{..., "session_id": "abc"} 这种把聊天接口的
    # body 顺手复用过来的调用,静默吞掉字段比报错更难查 —— 这条接口根本没有会话。
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1, description="一段售后描述,不能为空。")

    @field_validator("description")
    @classmethod
    def _reject_blank_description(cls, value: str) -> str:
        """与 `message` 同一条规则:纯空白既不是内容,也会白花一次上游调用。"""
        return _reject_blank(value, "description")


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


@router.post("/extract")
async def extract(req: ExtractRequest) -> AfterSalesTicket:
    """把售后描述抽成结构化工单要素。模型没给出结构化结果时返回 422 + 原始输出。

    **返回注解 `-> AfterSalesTicket` 是承重的,别拆**:FastAPI 拿它当 response_model,
    于是(1)枚举按 JSON 序列化成「换货」这样的中文值,不是 `AfterSalesIntent.exchange`
    ——手搓响应体、对枚举做 f-string 都会在这里变成 500;(2)nullable 字段的 `null`
    靠它兜住:`order_id` / `reason` 为 None 时给的是 `null` 而不是把键丢掉。

    第 (2) 条尤其容易搞反:`response_model_exclude_none` 的默认值是 `False`,
    **正是这个默认**(而不是某个显式开关)保住了 null —— 一旦加上
    `response_model_exclude_none=True`,两个键会整段消失,
    `test_null_fields_are_preserved_in_the_response` 会红。
    反过来,service 侧写 `model_dump(exclude_none=True)` 是吃不掉的:响应模型会拿 dict
    重新校验、把默认值补回来 —— 开关在装饰器这一层,不在 service 那边。

    与 `/chat/stream` 不同,这条接口是无状态的一次性调用:没有 session、没有历史。
    """
    result = await extract_ticket(req.description)
    if result.ticket is None:
        # 上游是通的,是模型没按工具约定给结果 —— 与 5xx 区分开:重试一条更具体的描述有意义。
        # detail 里带上模型这一轮的原始输出(spec §十 的"422 + 原始返回文本"):
        # 固定一句话只能让人猜,原文才说得清"它到底说了什么"。
        raise HTTPException(
            status_code=422,
            detail=(
                "模型未能返回结构化结果,请换一段更具体的描述重试。"
                f"模型原始输出:{_embed_raw(result.raw)}"
            ),
        )
    return result.ticket
