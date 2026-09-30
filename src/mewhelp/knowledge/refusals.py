"""Durable refusal records, committed independently of the message ledger."""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, Session, mapped_column

from mewhelp.db.base import Base
from mewhelp.db.models import BIGINT_PK

ReasonCode = Literal[
    "no_evidence", "low_relevance", "insufficient_evidence", "invalid_generation",
    "invalid_citation", "unsupported_context_size",
]
REASON_CODES = (
    "no_evidence", "low_relevance", "insufficient_evidence", "invalid_generation",
    "invalid_citation", "unsupported_context_size",
)
ENTRY_POINTS = ("chat_stream", "agent", "cli")
TRIGGER_STAGES = ("retrieval", "generation")


def utc_now() -> dt.datetime:
    # MySQL DATETIME has no timezone; this column always stores naive UTC.
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


class LowConfidenceQuestion(Base):
    __tablename__ = "low_confidence_questions"
    __table_args__ = (
        Index("idx_low_confidence_conversation", "source_conversation_id"),
        Index("idx_low_confidence_created_at", "created_at"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    original_question: Mapped[str] = mapped_column(Text, nullable=False)
    source_conversation_id: Mapped[int | None] = mapped_column(
        BIGINT_PK, ForeignKey("conversations.id", name="fk_low_confidence_conversation"),
    )
    entry_point: Mapped[str] = mapped_column(
        Enum(*ENTRY_POINTS, name="lcq_entry_point", create_constraint=True, validate_strings=True),
        nullable=False,
    )
    trigger_stage: Mapped[str] = mapped_column(
        Enum(*TRIGGER_STAGES, name="lcq_trigger_stage", create_constraint=True, validate_strings=True),
        nullable=False,
    )
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, server_default=text("CURRENT_TIMESTAMP"),
    )


@dataclass(frozen=True)
class RefusalInput:
    original_question: str
    source_conversation_id: int | None
    entry_point: Literal["chat_stream", "agent", "cli"]
    trigger_stage: Literal["retrieval", "generation"]
    reason_code: ReasonCode
    reason: str


class PoolCommitError(RuntimeError):
    """Refusal cannot be delivered as a recorded business result."""


def record_refusal(session_factory: Callable[[], Session], item: RefusalInput) -> str:
    if item.entry_point not in ENTRY_POINTS or item.trigger_stage not in TRIGGER_STAGES:
        raise ValueError("Invalid refusal entry_point or trigger_stage")
    if item.reason_code not in REASON_CODES:
        raise ValueError("Invalid refusal reason_code")
    if item.entry_point != "cli" and item.source_conversation_id is None:
        raise ValueError("Online refusal requires source conversation")
    if not item.original_question.strip() or not item.reason.strip():
        raise ValueError("Refusal question and reason must be nonempty")
    try:
        with session_factory() as session:
            row = LowConfidenceQuestion(
                original_question=item.original_question,
                source_conversation_id=item.source_conversation_id,
                entry_point=item.entry_point, trigger_stage=item.trigger_stage,
                reason_code=item.reason_code, reason=item.reason, created_at=utc_now(),
            )
            session.add(row)
            session.flush()
            row_id = str(row.id)
            session.commit()
            return row_id
    except Exception as exc:
        raise PoolCommitError("Unable to commit low-confidence question") from exc
