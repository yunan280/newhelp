from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TicketResumeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    session_id: str = Field(min_length=1, max_length=64)
    user_id: str | None = Field(default=None, min_length=1, max_length=64)
    confirmation_id: str = Field(min_length=1, max_length=64)
    action: Literal['confirm', 'cancel']

    @field_validator('session_id', 'user_id', 'confirmation_id')
    @classmethod
    def nonblank(cls, value):
        if value is not None and not value.strip():
            raise ValueError('字段不可空白')
        return value

    @property
    def resolved_user_id(self):
        return self.user_id or 'demo-user'
