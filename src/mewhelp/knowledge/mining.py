"""历史对话分批抽取到暂存，整批去重发布后推进水位。"""

import hashlib
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from mewhelp.db.models import Message, MsgRole

from .store import KnowledgeChunk, KnowledgeDraft, QaStaging, put_chunk, source_id


class ExtractedQa(BaseModel):
    source_conversation_id: int = Field(description="问答来自哪个输入会话编号")
    question: str = Field(description="用户真实或忠实改写的问法")
    answer: str = Field(description="有对话证据支持、可独立理解的答案")


class ExtractedBatch(BaseModel):
    items: list[ExtractedQa] = Field(description="可复用问答，无法确认事实时返回空列表")


@dataclass(frozen=True)
class ConversationSample:
    conversation_id: int
    text: str


def extraction_prompt(samples: list[ConversationSample]) -> str:
    transcript = "\n\n".join(f"会话 {s.conversation_id}:\n{s.text}" for s in samples)
    return (
        "从客服对话提取可复用的问答。只依据客服明确给出的政策或处理办法，"
        "不编造、不保留订单号、姓名、电话、地址等个人信息。"
        "个案处理、承诺不清或答案冲突时跳过。question 使用真实问法，"
        "source_conversation_id 必须是输入会话编号。\n\n" + transcript
    )


def llm_extract(samples: list[ConversationSample]) -> list[ExtractedQa]:
    from mewhelp.llm import get_structured_model

    result = get_structured_model(ExtractedBatch).invoke(extraction_prompt(samples))
    return result.items


_PRIVATE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)\d{10,}(?!\d)|(?:收货地址|身份证|联系电话|手机号)[:：]")


def _normalized(text: str) -> str:
    return re.sub(r"\s+", "", text).casefold().strip("？?。.!！")


def publish_batch(session: Session, batch_key: str) -> int:
    """对暂存全批按问法归组；有相互冲突的答案就留在暂存待人工看。"""
    rows = session.scalars(select(QaStaging).where(QaStaging.batch_no == batch_key, QaStaging.status == "extracted").order_by(QaStaging.id)).all()
    grouped: dict[str, list[QaStaging]] = {}
    for row in rows:
        grouped.setdefault(_normalized(row.question), []).append(row)
    published = 0
    for question, group in grouped.items():
        if not question or any(_PRIVATE.search(r.question + r.answer) for r in group):
            for row in group:
                row.status = "discarded"
            continue
        answers = {_normalized(row.answer) for row in group}
        if len(answers) != 1 or not next(iter(answers)):
            for row in group:
                row.status = "discarded"
            continue
        chosen = group[0]
        source_key = "mined:" + hashlib.sha256(question.encode("utf-8")).hexdigest()
        existing = session.get(KnowledgeChunk, source_id(source_key))
        if existing is not None:
            # 跨定时批次仍要去重；冲突答案不能覆盖已经发布的知识。
            for row in group:
                row.status = "discarded"
            continue
        put_chunk(
            session,
            KnowledgeDraft(source_key, "客服对话", chosen.question, chosen.answer, None, "faq"),
        )
        for row in group:
            row.status = "kept" if row is chosen else "discarded"
        published += 1
    return published


def mine_conversations(
    session_factory: Callable[[], Session],
    extract: Callable[[list[ConversationSample]], list[ExtractedQa]] = llm_extract,
    *,
    batch_size: int = 100,
) -> int:
    """每次对全量现有消息分批抽取；最终主键幂等可安全重跑。"""
    if batch_size <= 0:
        raise ValueError("batch_size 必须为正")
    batch_key = uuid.uuid4().hex
    with session_factory() as session:
        # 上次若停在“暂存已提交、整批发布前”，丢弃不完整批次并重新抽取。
        # 已发布批次在同一事务内改为 kept/discarded，不受此清理影响。
        session.execute(delete(QaStaging).where(QaStaging.status == "extracted"))
        session.commit()
        messages = session.scalars(
            select(Message)
            .where(Message.role.in_([MsgRole.user, MsgRole.assistant]))
            .order_by(Message.id)
        ).all()
        snapshot = [(m.id, m.conversation_id, m.role.value, m.content) for m in messages if m.content]
        processed_refs = set(session.scalars(
            select(QaStaging.source_ref).where(QaStaging.status.in_(["kept", "discarded"]))
        ).all())
    if not snapshot:
        return 0
    by_conversation: dict[int, list[str]] = {}
    latest: dict[int, int] = {}
    for message_id, conversation_id, role, content in snapshot:
        by_conversation.setdefault(conversation_id, []).append(f"{role}: {content}")
        latest[conversation_id] = message_id
    all_samples = [
        ConversationSample(cid, "\n".join(lines))
        for cid, lines in by_conversation.items()
        if f"{cid}:{latest[cid]}" not in processed_refs
    ]
    for start in range(0, len(all_samples), batch_size):
        samples = all_samples[start : start + batch_size]
        candidates = extract(samples)
        valid_ids = {sample.conversation_id for sample in samples}
        with session_factory() as session:
            for candidate in candidates:
                if candidate.source_conversation_id not in valid_ids:
                    continue
                session.add(
                    QaStaging(
                        batch_no=batch_key,
                        source_ref=f"{candidate.source_conversation_id}:{latest[candidate.source_conversation_id]}",
                        question=candidate.question,
                        answer=candidate.answer,
                        status="extracted",
                    )
                )
            session.commit()
    with session_factory() as session:
        published = publish_batch(session, batch_key)
        for sample in all_samples:
            session.add(QaStaging(
                batch_no=batch_key,
                source_ref=f"{sample.conversation_id}:{latest[sample.conversation_id]}",
                question="",
                answer="",
                status="discarded",
            ))
        session.commit()
    return published
