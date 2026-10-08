from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages

from mewhelp.ch06.config import Ch06Settings
from mewhelp.ch07.config import BudgetProfile, ContextSettings

from .limits import AgentLimits


class WorkflowState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    history_ctx: dict
    history_epoch: str
    question: str
    resolved_question: str
    session_id: str
    user_id: str
    conversation_id: int
    resumed: bool
    turn_id: str
    trace_id: str | None
    origin_trace_id: str | None
    intent: str
    route: str
    evidence: dict | None
    gate: dict | None
    retrieved_chunks: dict | None
    retrieval_performed: bool
    retrieval_events: list[dict]
    answer_message_id: str | None
    feedback_status: str
    knowledge_raw_usage: dict | None
    knowledge_assessment: dict | None
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
    assessment_control: dict
    user_facts: dict
    refund_offer: dict | None
    refund_offers: dict[str, dict]
    refund_receipts: dict[str, dict]
    matched_tool: str | None
    tool_catalog: list[dict]
    tool_catalog_hash: str
    ticket_request: dict
    tool_queue: list[dict]
    tool_cursor: int
    tool_results: list[dict]
    ticket_preview: dict | None
    ticket_status: str | None
    ticket_confirmation_receipts: dict[str, dict]
    ticket_prepared: dict | None
    ticket_resume: dict | None
    ticket_receipt: dict | None


@dataclass
class WorkflowContext:
    session_factory: Callable
    model_factory: Callable
    rag_factory: Callable
    limits: AgentLimits
    router_settings: Ch06Settings = field(default_factory=Ch06Settings, kw_only=True)
    router_model_factory: Callable | None = field(default=None, kw_only=True)
    settings: ContextSettings = field(default_factory=ContextSettings, kw_only=True)
    profile: BudgetProfile = field(default_factory=BudgetProfile, kw_only=True)
    summary_manager: object | None = field(default=None, kw_only=True)
    request_epoch: str | None = field(default=None, kw_only=True)
    request_history: dict | None = field(default=None, kw_only=True)
    tool_runtime: object | None = field(default=None, kw_only=True)
    tool_snapshot: object | None = field(default=None, kw_only=True)
    observation_runtime: object | None = field(default=None, kw_only=True)
    confidence_profile: object | None = field(default=None, kw_only=True)
