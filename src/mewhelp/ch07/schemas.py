import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field


class ConversationItem(BaseModel):
    id: int
    session_id: str
    created_at: dt.datetime
    updated_at: dt.datetime
    first_question: str
    has_summary: bool
    summary_count: int


class ConversationList(BaseModel):
    conversations: list[ConversationItem]


class VisibleMessage(BaseModel):
    id: int
    role: Literal['user', 'assistant']
    content: str
    citations: list[dict] = Field(default_factory=list)
    answer_message_id: str | None = None
    feedback_status: Literal['none','down'] = 'none'


class ConversationMessages(BaseModel):
    id: int
    session_id: str
    messages: list[VisibleMessage]
