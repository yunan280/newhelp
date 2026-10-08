from dataclasses import asdict, dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


@dataclass(frozen=True)
class RequestTraceContext:
    session_id: str | None = None
    user_id: str | None = None
    conversation_id: int | None = None
    turn_id: str | None = None
    entry_point: str = 'agent'
    trace_kind: Literal['chat', 'flywheel', 'evaluation'] = 'chat'
    origin_trace_id: str | None = None

    def metadata(self) -> dict:
        return {key: value for key, value in asdict(self).items() if value is not None}


class RetrievedChunk(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    rank: int = Field(ge=1)
    chunk_id: str = Field(pattern=r'^[1-9][0-9]*$')
    text: str
    questions: str
    answer: str
    section_path: str | None = None
    content_hash: str | None = None
    relevance_score: float | None = Field(default=None, ge=0, le=1, allow_inf_nan=False)
    category: str | None = None
    product_category: str | None = None
    content_type: str | None = None
    is_key_clause: bool | None = None


class EvidenceSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    schema_version: Literal[1] = 1
    state: Literal['captured', 'empty', 'legacy_partial', 'unavailable']
    chunks: list[RetrievedChunk] = Field(default_factory=list)
    query: str | None = None
    filters: dict = Field(default_factory=dict)
    top_k: int = Field(default=5, ge=1)
    confidence: dict | None = None
    reason: str | None = None

    @model_validator(mode='after')
    def valid_evidence(self):
        if self.state == 'captured' and (not self.chunks or
                                        any(c.relevance_score is None for c in self.chunks)):
            raise ValueError('captured evidence requires original ranked chunks with scores')
        if self.state in {'empty', 'unavailable'} and self.chunks:
            raise ValueError('empty/unavailable evidence cannot contain invented chunks')
        if self.state == 'unavailable' and not self.reason:
            raise ValueError('unavailable evidence requires an explanation')
        if len({c.chunk_id for c in self.chunks}) != len(self.chunks):
            raise ValueError('snapshot chunks must be unique')
        if [c.rank for c in self.chunks] != list(range(1, len(self.chunks) + 1)):
            raise ValueError('snapshot ranks must preserve actual contiguous order')
        return self


class MessageSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    schema_version: Literal[1] = 1
    retrieved_chunks: EvidenceSnapshot | None = None
    turn_id: str
    source_user_event_key: str
    source_user_message_id: str | None = Field(default=None, pattern=r'^[1-9][0-9]*$')
    intent: str
    retrieval_performed: bool | None
    retrieval_events: list[dict] = Field(default_factory=list)
    pool_id: str | None = None
    trace_id: str | None = None
    answer_status: Literal['completed', 'waiting', 'error']
    feedback_lcq_id: str | None = None
