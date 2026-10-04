from dataclasses import dataclass

from langchain_core.messages import AnyMessage

from .budget import ContextBudget


@dataclass(frozen=True)
class LedgerMessage:
    id: int
    role: str
    content: str
    citations: tuple[dict, ...] = ()
    event_key: str | None = None


@dataclass(frozen=True)
class SummarySegment:
    id: int
    seq: int
    from_msg_id: int
    upto_msg_id: int
    content: str


@dataclass(frozen=True)
class ConversationSnapshot:
    conversation_id: int
    session_id: str
    user_id: str
    summary_upto_msg_id: int
    layer1_from_msg_id: int
    messages: tuple[LedgerMessage, ...]
    summaries: tuple[SummarySegment, ...]


@dataclass(frozen=True)
class HistoryTurn:
    turn_id: str
    from_msg_id: int
    upto_msg_id: int
    messages: tuple[AnyMessage, ...]


@dataclass(frozen=True)
class HistoryContext:
    conversation_id: int
    summary_upto_msg_id: int
    layer1_from_msg_id: int
    summary: str
    summary_segments: tuple[SummarySegment, ...]
    layer2: tuple[AnyMessage, ...]
    layer1: tuple[AnyMessage, ...]
    turns: tuple[HistoryTurn, ...]
    tokens: dict[str, int]
    budget: ContextBudget


@dataclass(frozen=True)
class SummaryJob:
    conversation_id: int
    old_upto_msg_id: int
    layer1_snapshot_id: int
    turns: tuple[HistoryTurn, ...]


@dataclass(frozen=True)
class SummaryResult:
    content: str
    usage: dict[str, int]
    elapsed_ms: int
