from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mewhelp.ch02.schemas import AgentRequest
from mewhelp.knowledge.answering import SourceDTO

from .limits import TokenUsage

Intent = Literal["物流", "订单", "商品咨询", "退款退货", "售后", "投诉", "闲聊"]
Route = Literal["knowledge", "business", "complaint", "chitchat"]
Action = Literal["handoff", "create_ticket"]
TicketKind = Literal["售后", "投诉", "咨询"]
TurnRequest = AgentRequest


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
