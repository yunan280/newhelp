"""请求体。

`_reject_blank` 从 ch01/api.py 复用 —— **不复制一份**。那条规则的细节(零宽字符
不属于 `str.isspace()`,所以 `strip()` 拦不住它们)是花了两次返工换来的,
复制出去就意味着将来只有一个入口被修。

`extra="forbid"` 也照搬:把 `session_id` 拼错成别的名字时,看着一切正常,
实际每一轮都在开新会话。
"""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mewhelp.ch01.api import _reject_blank
from mewhelp.knowledge.filters import SearchFilters

# 占位身份。本章没有登录,聊天页也没有身份 —— 见 spec §6.4,
# 造假鉴权比留一个诚实的占位更糟。
DEFAULT_USER_ID = "demo-user"


class _TurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = Field(
        default=None,
        description="会话 id。不传则服务端生成,并从 session 事件返回。",
    )
    user_id: str | None = Field(default=None, description="用户标识,本章是占位。")
    message: str = Field(min_length=1, description="用户这一轮说的话,不能为空。")
    filters: SearchFilters | None = None

    @field_validator("session_id")
    @classmethod
    def _check_session_id(cls, value: str | None) -> str | None:
        return _reject_blank(value, "session_id")

    @field_validator("user_id")
    @classmethod
    def _check_user_id(cls, value: str | None) -> str | None:
        return _reject_blank(value, "user_id")

    @field_validator("message")
    @classmethod
    def _check_message(cls, value: str) -> str:
        return _reject_blank(value, "message")

    @property
    def resolved_user_id(self) -> str:
        return self.user_id or DEFAULT_USER_ID


class ChatRequest(_TurnRequest):
    """SSE 出口的请求体。"""


class AgentRequest(_TurnRequest):
    """JSON 出口的请求体。与 ChatRequest 同形 —— 两个出口共用同一个核心,
    请求体不该长得不一样。"""
