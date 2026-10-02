from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from mewhelp.ch06.config import Ch06Settings

from .limits import AgentLimits


class WorkflowState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    question: str
    resolved_question: str
    session_id: str
    user_id: str
    conversation_id: int
    resumed: bool
    turn_id: str
    intent: str
    route: str
    evidence: dict | None
    gate: dict | None
    agent_messages: list[AnyMessage]
    pending_tool_calls: list[dict]
    tool_trace: list[dict]
    decision_count: int
    tool_count: int
    usage: dict
    calls: dict[str, int]
    answer: str
    actions: list[str]
    offers: dict[str, dict]
    ticket_receipts: dict[str, dict]
    node_trace: list[str]
    stop_reason: str
    ledger_error: str | None
    started_at: float
    # Explicit transport/control fields are transient and reset at begin_turn.
    filters: dict
    entry_point: str
    decision: dict | None
    refused: bool
    low_confidence_question_id: str | None
    offer: dict | None
    status: str
    scope: str
    intent_confidence: float | None
    trusted_order_id: str | None
    trusted_entities: list[dict]
    understanding: dict
    classification: dict
    expansion: dict
    queries: list[str]
    order: dict | None
    order_selection: dict | None
    selection_status: str | None
    selected_order_id: str | None
    selection_receipts: dict[str, dict]
    assessment: dict | None
    refund_offer: dict | None
    refund_offers: dict[str, dict]
    refund_receipts: dict[str, dict]


@dataclass
class WorkflowContext:
    session_factory: Callable
    model_factory: Callable
    rag_factory: Callable
    limits: AgentLimits
    router_settings: Ch06Settings = field(default_factory=Ch06Settings, kw_only=True)
    router_model_factory: Callable | None = field(default=None, kw_only=True)
