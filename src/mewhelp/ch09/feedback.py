"""One durable feedback event per owned, committed final answer."""

import asyncio
import inspect
from typing import Literal

from langchain_core.messages import AIMessage
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from mewhelp.ch05.workflow import _event_key
from mewhelp.db.models import Conversation, Message, MsgRole
from mewhelp.knowledge.refusals import LowConfidenceQuestion, PoolCommitError, utc_now

from .contracts import EvidenceSnapshot, MessageSnapshot, RetrievedChunk
from .snapshots import resolve_evidence_snapshot


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    user_id: str = Field(min_length=1, max_length=64)
    session_id: str = Field(min_length=1, max_length=64)
    answer_message_id: str = Field(pattern=r"^[1-9][0-9]*$", max_length=20)
    choice: Literal["down"]

    @field_validator("user_id", "session_id")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("身份字段不能为空白")
        return value

    @field_validator("answer_message_id")
    @classmethod
    def unsigned_id(cls, value):
        if int(value) >= 2**64:
            raise ValueError("回答ID超出BIGINT UNSIGNED范围")
        return value


class FeedbackReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)
    pool_id: str
    answer_message_id: str
    replayed: bool


def _owned_answer(db, request, *, lock=False):
    stmt = (
        select(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .where(
            Message.id == int(request.answer_message_id),
            Conversation.user_id == request.user_id,
            Conversation.session_id == request.session_id,
        )
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    row = db.scalar(stmt)
    if row is None:
        raise LookupError("回答不存在")
    if row.role is not MsgRole.assistant or row.tool_calls or not row.content:
        raise ValueError("只能反馈已提交的最终回答")
    return row


def _prepare(factory, request):
    with factory() as db:
        row = _owned_answer(db, request)
        return (
            MessageSnapshot.model_validate(row.retrieval_snapshot)
            if row.retrieval_snapshot
            else None
        )


def _commit(factory, request, recovered):
    try:
        with factory() as db:
            row = _owned_answer(db, request, lock=True)
            snapshot = (
                MessageSnapshot.model_validate(row.retrieval_snapshot)
                if row.retrieval_snapshot
                else recovered
            )
            if snapshot is None or snapshot.answer_status != "completed":
                raise ValueError("只能反馈已提交的最终回答")
            user = (
                db.get(Message, int(snapshot.source_user_message_id))
                if snapshot.source_user_message_id
                else None
            )
            if (
                user is None
                or user.role is not MsgRole.user
                or user.conversation_id != row.conversation_id
                or user.id >= row.id
                or (user.ch06_event_key and user.ch06_event_key != snapshot.source_user_event_key)
            ):
                raise ValueError("无法确认回答与该轮用户原话的绑定")
            if snapshot.feedback_lcq_id:
                if db.get(LowConfidenceQuestion, int(snapshot.feedback_lcq_id)) is None:
                    raise ValueError("反馈标记对应的问题池记录不存在")
                return FeedbackReceipt(
                    pool_id=snapshot.feedback_lcq_id,
                    answer_message_id=request.answer_message_id,
                    replayed=True,
                )
            pool = LowConfidenceQuestion(
                original_question=user.content,
                source_conversation_id=row.conversation_id,
                entry_point="feedback",
                trigger_stage="feedback",
                reason_code="user_feedback",
                reason=f"用户表示回答未解决；回答ID={row.id}",
                created_at=utc_now(),
                retrieved_chunks=snapshot.retrieved_chunks.model_dump(mode="json")
                if snapshot.retrieved_chunks
                else None,
            )
            db.add(pool)
            db.flush()
            pool_id = str(pool.id)
            row.retrieval_snapshot = snapshot.model_copy(
                update={"feedback_lcq_id": pool_id}
            ).model_dump(mode="json")
            db.commit()
            return FeedbackReceipt(
                pool_id=pool_id, answer_message_id=request.answer_message_id, replayed=False
            )
    except (LookupError, ValueError):
        raise
    except Exception as exc:
        raise PoolCommitError("反馈未能提交，请重试") from exc


async def submit_negative_feedback(factory, request, *, recover):
    snapshot = await asyncio.to_thread(_prepare, factory, request)
    if snapshot is None:
        if recover is None:
            raise ValueError("旧回答缺少可证明的用户原话绑定")
        snapshot = await recover(int(request.answer_message_id))
    return await asyncio.to_thread(_commit, factory, request, snapshot)


def _legacy_record(factory, message_id):
    with factory() as db:
        row = db.get(Message, message_id)
        if row is None:
            raise LookupError("回答不存在")
        if row.role is not MsgRole.assistant or row.tool_calls or not row.content:
            raise ValueError("只能反馈已提交的最终回答")
        if row.retrieval_snapshot:
            return MessageSnapshot.model_validate(row.retrieval_snapshot), None
        session_id = db.scalar(
            select(Conversation.session_id).where(Conversation.id == row.conversation_id)
        )
        users = {
            m.id: (m.ch06_event_key, m.content)
            for m in db.scalars(
                select(Message).where(
                    Message.conversation_id == row.conversation_id,
                    Message.role == MsgRole.user,
                    Message.id < row.id,
                )
            )
        }
        return None, {
            "id": row.id,
            "key": row.ch06_event_key,
            "citations": row.citations,
            "session_id": session_id,
            "users": users,
        }


def _bound_turn(values, record):
    for message in values.get("messages", []):
        if not isinstance(message, AIMessage):
            continue
        meta = message.additional_kwargs.get("ch07", {})
        if str(meta.get("ledger_id")) == str(record["id"]) and meta.get("turn_id"):
            uid = meta.get("from_msg_id")
            if uid and int(uid) in record["users"]:
                return (
                    int(uid),
                    meta["turn_id"],
                    bool((message.id or "").endswith(("waiting", "waiting-ticket"))),
                )
    turn = values.get("turn_id")
    if turn and record["key"]:
        for phase in ("complete", "cancel", "waiting", "waiting_ticket"):
            if record["key"] == _event_key(turn, phase, "answer"):
                for uid, (key, _) in record["users"].items():
                    if key == _event_key(turn, "user", 0):
                        return uid, turn, phase.startswith("waiting")
    return None


def _legacy_evidence(values, record, turn):
    if values.get("turn_id") != turn:
        return resolve_evidence_snapshot(None, citations=record["citations"]), None
    performed = values.get("retrieval_performed")
    raw = values.get("retrieved_chunks") or (values.get("evidence") or {}).get("retrieved_chunks")
    if raw:
        return EvidenceSnapshot.model_validate(raw), True
    envelope = values.get("evidence")
    if envelope is not None and "sources" in envelope:
        chunks = []
        scores = envelope.get("scores") or []
        for rank, source in enumerate(envelope["sources"], 1):
            chunks.append(
                RetrievedChunk(
                    rank=rank,
                    chunk_id=source["chunk_id"],
                    text=f"分类：{source.get('category', '')}\n问题：{source['questions']}\n答案：{source['answer']}",
                    questions=source["questions"],
                    answer=source["answer"],
                    section_path=source.get("section_path"),
                    content_hash=source.get("content_hash"),
                    category=source.get("category"),
                    relevance_score=scores[rank - 1]
                    if len(scores) == len(envelope["sources"])
                    else None,
                )
            )
        state = (
            "empty"
            if not chunks
            else "legacy_partial"
            if any(c.relevance_score is None for c in chunks)
            else "captured"
        )
        return EvidenceSnapshot(
            state=state,
            chunks=chunks,
            query=values.get("resolved_question"),
            reason="原记录缺少精排评分" if state == "legacy_partial" else None,
        ), True
    if (
        performed is None
        and values.get("route") in {"chitchat", "business", "complaint", "other"}
        and "log_turn" in values.get("node_trace", [])
        and not any(n.startswith("retrieve_") for n in values["node_trace"])
    ):
        performed = False
    return resolve_evidence_snapshot(
        None, retrieval_performed=performed, citations=record["citations"]
    ), performed


async def recover_answer_snapshot(message_id, *, factory, checkpoint_reader):
    saved, record = await asyncio.to_thread(_legacy_record, factory, message_id)
    if saved:
        return saved
    history = checkpoint_reader(record["session_id"])
    if inspect.isawaitable(history):
        history = await history
    if hasattr(history, "__aiter__"):
        states = [s async for s in history]
    elif isinstance(history, list):
        states = history
    else:
        states = [history]
    fallback = None
    for state in states:
        values = state if isinstance(state, dict) else state.values
        binding = _bound_turn(values, record)
        if binding is None:
            continue
        uid, turn, waiting = binding
        if waiting:
            raise ValueError("只能反馈已提交的最终回答")
        evidence, performed = _legacy_evidence(values, record, turn)
        snapshot = MessageSnapshot(
            turn_id=turn,
            source_user_message_id=str(uid),
            source_user_event_key=record["users"][uid][0] or f"user-message:{uid}",
            intent=values.get("intent", "未知") if values.get("turn_id") == turn else "未知",
            retrieval_performed=performed,
            retrieved_chunks=evidence,
            answer_status="completed",
        )
        if evidence is None and performed is False:
            return snapshot
        if evidence and evidence.state in {"captured", "empty"}:
            return snapshot
        if fallback is None or (evidence and evidence.state == "legacy_partial"):
            fallback = snapshot
    if fallback:
        return fallback
    raise ValueError("旧回答缺少可证明的用户原话绑定")
