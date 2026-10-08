from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from mewhelp.ch01.api import _reject_blank
from mewhelp.ch02.schemas import AgentRequest
from mewhelp.knowledge.answering import SourceDTO

from .limits import TokenUsage

Intent = Literal["物流", "订单", "商品咨询", "退款退货", "售后", "投诉", "闲聊", "其他"]
Route = Literal["knowledge", "business", "complaint", "chitchat", "aftersales", "other"]
QueryScope = Literal["general", "order_specific"]
RefundReason = Literal["七天无理由", "商品质量问题", "与描述不符", "破损或缺件", "发错货", "其他"]
Confidence = Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
Action = Literal["handoff", "create_ticket"]
TicketKind = Literal["售后", "投诉", "咨询"]
TurnRequest = AgentRequest


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntentOutput(StrictDTO):
    intent: Intent
    confidence: Confidence
    matched_tool: str | None = Field(default=None, max_length=128)


class UnderstandingOutput(StrictDTO):
    question: str = Field(min_length=1, max_length=4000)
    scope: QueryScope
    reference_order_id: str | None
    reference_message_id: str | None

    @field_validator("question")
    @classmethod
    def nonblank_question(cls, value):
        return _reject_blank(value, "question")


class ExpansionOutput(StrictDTO):
    queries: list[str] = Field(min_length=2, max_length=4)

    @field_validator("queries")
    @classmethod
    def distinct_nonblank(cls, value):
        queries = [query.strip() for query in value]
        if any(not query or len(query) > 500 for query in queries):
            raise ValueError("queries must be nonblank and bounded")
        if len(set(queries)) != len(queries):
            raise ValueError("queries must have distinct focuses")
        return queries


class OrderDTO(StrictDTO):
    order_id: str
    user_id: str
    product_name: str
    product_category: str
    status: str
    paid_amount: Decimal = Field(ge=0, allow_inf_nan=False)
    ordered_at: date
    received_at: date | None
    condition: Literal["unopened", "opened", "unknown"]
    as_of: date


class OrderSelection(StrictDTO):
    selection_id: str
    turn_id: str
    orders: list[OrderDTO]


class OrderResumeRequest(StrictDTO):
    session_id: str = Field(min_length=1, max_length=64)
    user_id: str | None = Field(default=None, min_length=1, max_length=64)
    selection_id: str = Field(min_length=1, max_length=64)
    order_id: str = Field(min_length=1, max_length=64)

    @field_validator("session_id", "user_id", "selection_id", "order_id")
    @classmethod
    def nonblank(cls, value):
        return _reject_blank(value, "selection field")

    @property
    def resolved_user_id(self):
        return self.user_id or "demo-user"


class OrderAssessment(StrictDTO):
    verdict: Literal["eligible", "ineligible", "needs_clarification"]
    explanation: str = Field(min_length=1, max_length=3000)
    missing_facts: list[str] = Field(default_factory=list, max_length=8)


class RefundOffer(StrictDTO):
    offer_id: str
    turn_id: str
    order: OrderDTO
    question: str
    assessment: OrderAssessment
    sources: list[SourceDTO]


class RefundRequest(StrictDTO):
    session_id: str = Field(min_length=1, max_length=64)
    user_id: str | None = Field(default=None, min_length=1, max_length=64)
    offer_id: str = Field(min_length=1, max_length=64)
    order_id: str = Field(min_length=1, max_length=64)
    reason: RefundReason
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_true(cls, value):
        if value is not True:
            raise ValueError("explicit boolean confirmation required")
        return value

    @field_validator("session_id", "user_id", "offer_id", "order_id")
    @classmethod
    def nonblank(cls, value):
        return _reject_blank(value, "refund field")

    @property
    def resolved_user_id(self):
        return self.user_id or "demo-user"


class RefundReceipt(StrictDTO):
    application_no: str
    order_id: str
    reason: RefundReason
    status: Literal["pending"] = "pending"
    replayed: bool


class AgentDecision(StrictDTO):
    reply_mode: Literal["answer", "clarify"]
    suggested_actions: list[Action] = Field(default_factory=list)
    ticket_type: TicketKind | None = None


class ActionOffer(StrictDTO):
    offer_id: str
    turn_id: str
    actions: list[Action]
    description: str
    ticket_type: TicketKind


class ToolTrace(StrictDTO):
    call_id: str
    round: int
    name: str
    args: dict
    ok: bool
    content: str
    error: str | None
    elapsed_ms: int


class TurnResult(StrictDTO):
    session_id: str
    conversation_id: int
    resumed: bool
    answer: str
    sources: list[SourceDTO] = Field(default_factory=list)
    refused: bool = False
    low_confidence_question_id: str | None = None
    answer_message_id: str | None = None
    feedback_status: Literal['none', 'down'] = 'none'
    intent: Intent
    route: Route
    actions: list[Action] = Field(default_factory=list)
    offer: ActionOffer | None = None
    tool_trace: list[ToolTrace] = Field(default_factory=list)
    node_trace: list[str]
    usage: TokenUsage
    calls: dict[str, int]
    stop_reason: str
    ledger_error: str | None = None
    status: Literal["completed", "waiting_for_order", "waiting_for_ticket"] = "completed"
    ticket_preview: dict | None = None
    ticket_receipt: dict | None = None
    resolved_question: str = ""
    intent_confidence: Confidence | None = None
    order_selection: OrderSelection | None = None
    order: OrderDTO | None = None
    assessment: OrderAssessment | None = None
    refund_offer: RefundOffer | None = None


class TicketRequest(StrictDTO):
    session_id: str = Field(min_length=1, max_length=64)
    user_id: str | None = Field(default=None, min_length=1, max_length=64)
    offer_id: str = Field(min_length=1, max_length=64)
    confirmed: Literal[True]
    description: str = Field(min_length=1, max_length=2000)
    ticket_type: TicketKind

    @field_validator("confirmed", mode="before")
    @classmethod
    def explicit_true(cls, value):
        if value is not True:
            raise ValueError("explicit boolean confirmation required")
        return value

    @field_validator("session_id", "user_id", "offer_id", "description")
    @classmethod
    def nonblank(cls, value):
        return _reject_blank(value, "ticket field")

    @property
    def resolved_user_id(self):
        return self.user_id or "demo-user"


class TicketReceipt(StrictDTO):
    ticket_no: str
    ticket_type: TicketKind
    replayed: bool
