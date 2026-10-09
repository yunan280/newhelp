"""会话 / 消息 / FAQ / 工单的读写 —— SQLAlchemy 只在这一层出现。

上层(编排层、工具)拿到的是这一层的函数,不直接碰 ORM,这样"回放该带哪些消息"
这类规则只有一个地方定义。

**这一层不 commit。** 一个回合要写 4 行消息(用户 / 带 tool_calls 的 assistant /
tool / 最终 assistant),中间任何一步失败都应该整轮不落库 —— 落一半的话下一轮回放
会读到一串残缺的上下文。所以事务边界留给调用方(T13 的编排层),这里只 flush。
"""

import datetime as dt
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from .models import Conversation, ConvStatus, Faq, Message, MsgRole, Ticket, TicketType

# LIKE 的转义符。反斜杠是 MySQL 与 SQLite 共同认的那个。
_LIKE_ESCAPE = "\\"


def _escape_like(value: str) -> str:
    """把用户给的 keyword 里的通配符转成字面量。

    顺序不能反:必须先转义符本身,再转 `%` 和 `_` —— 否则第一遍插进去的
    反斜杠会被第二遍再转一次,`%` 反而漏网。

    不转义的话 `LIKE '%%%'` 命中全表:模型抽关键词时给出 `%` 完全可能,
    用户也会直接问「% 是什么意思」。命中全部 FAQ 之后模型会拿着一堆
    不相关的政策编答案,而且**没有任何报错**。`_` 更隐蔽 —— 它匹配任意
    单字符,几乎所有条目都中。
    """
    return (
        value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )


@dataclass(frozen=True)
class TurnMessage:
    """一个回合里要落库的一行。

    独立于 ORM 模型,是为了让编排层不必 import 任何一个模型类就能组装回合 ——
    也让 T13 的表驱动测试能直接字面量地写出期望的 4 行。
    """

    role: MsgRole
    content: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    citations: list[dict] | None = None
    ch06_event_key: str | None = None
    retrieval_snapshot: dict | None = None


def append_messages_once(session: Session, *, conversation_id: int, rows: list[TurnMessage]) -> None:
    """Flush within the caller's transaction; only exact committed replays are accepted."""
    for row in rows:
        if row.ch06_event_key is None:
            append_messages(session, conversation_id=conversation_id, rows=[row])
            continue
        values = {"conversation_id": conversation_id, "role": row.role, "content": row.content,
                  "tool_calls": row.tool_calls, "tool_call_id": row.tool_call_id,
                  "citations": row.citations, "ch06_event_key": row.ch06_event_key}
        values['retrieval_snapshot'] = row.retrieval_snapshot
        dialect = session.get_bind().dialect.name
        if dialect == "sqlite":
            statement = sqlite_insert(Message).values(**values).on_conflict_do_nothing(
                index_elements=["ch06_event_key"])
        elif dialect == "mysql":
            statement = mysql_insert(Message).values(**values).on_duplicate_key_update(
                ch06_event_key=row.ch06_event_key)
        else:
            raise RuntimeError(f"unsupported message ledger dialect: {dialect}")
        session.execute(statement)
        saved = session.scalar(select(Message).where(Message.ch06_event_key == row.ch06_event_key)
                               .with_for_update().execution_options(populate_existing=True))
        from mewhelp.ch09.snapshots import same_message_snapshot
        if saved is not None and any([
            saved.conversation_id != conversation_id, saved.role != row.role,
            saved.content != row.content, saved.tool_calls != row.tool_calls,
            saved.tool_call_id != row.tool_call_id, saved.citations != row.citations,
            not same_message_snapshot(saved.retrieval_snapshot, row.retrieval_snapshot),
        ]):
            raise ValueError("message idempotency key reused with different content")


# ---------- 会话身份 ----------


def get_or_create_conversation(
    session: Session, *, session_id: str, user_id: str
) -> tuple[Conversation, bool]:
    """按 session_id 取会话,没有就建。返回 `(会话, 是否新建)`。

    **查到时不覆盖 user_id**:同一 session 换了 user_id 是异常,但本章不做鉴权,
    改写账本比保留异常更糟 —— 会话的归属会在中途换人,而之前那些消息还挂在
    原来那个人名下,账对不上。spec §6.4 承诺了不覆盖。

    这里显式 `flush()`(不是 commit):并发下两个请求可能同时 SELECT 到空、
    同时 INSERT,第二个会撞 `uk_session_id`。flush 让这个 IntegrityError 在
    **本函数内**抛出,调用方才能 `except IntegrityError` 后重查拿到已存在的那条。
    不 flush 的话(且 autoflush=False)冲突要拖到 commit 才爆,那时已经出了调用方
    的 try 块,重查路径成了死代码。
    """
    existing = (
        session.query(Conversation).filter(Conversation.session_id == session_id).one_or_none()
    )
    if existing is not None:
        return existing, False

    conv = Conversation(session_id=session_id, user_id=user_id, status=ConvStatus.ongoing)
    session.add(conv)
    session.flush()
    return conv, True


def set_conversation_status(session: Session, *, conversation_id: int, status: ConvStatus) -> None:
    """转人工 / 结束会话。整行 UPDATE,不经过 ORM 的对象状态。"""
    (
        session.query(Conversation)
        .filter(Conversation.id == conversation_id)
        .update({Conversation.status: status})
    )


# ---------- 消息 ----------


def append_messages(session: Session, *, conversation_id: int, rows: list[TurnMessage]) -> None:
    """把一回合的消息按给定顺序追加进去。空列表是合法的(纯工具调用失败的兜底)。"""
    for row in rows:
        session.add(
            Message(
                conversation_id=conversation_id,
                role=row.role,
                content=row.content,
                tool_calls=row.tool_calls,
                tool_call_id=row.tool_call_id,
                citations=row.citations,
                retrieval_snapshot=row.retrieval_snapshot,
            )
        )


def load_replay_messages(session: Session, *, conversation_id: int) -> list[Message]:
    """取**该跨轮回放**的消息 —— 比库里存的少。

    过滤规则(spec §11):
    - `role=user` 全留;
    - `role=assistant` 只在「有正文且没有 tool_calls」时留 —— 也就是只留最终回答;
    - `role=tool` 一条不留。

    为什么丢掉工具调用那一轮:上游返回 tool_calls 时**常常同时带一段正文**
    (实测 `"I'll look up the logistics information for order 1001."`)。把它回放
    给模型,它看到的是自己上一轮的半截前言,续接的语气和内容都会被带偏;而 tool
    消息对应的 tool_call_id 又不再有配对的 assistant 行,单独留下是非法序列。
    库里仍然存全量 —— 排查问题时要看的就是完整轨迹。
    """
    rows = (
        session.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.id)
        .all()
    )
    keep: list[Message] = []
    for row in rows:
        if row.role is MsgRole.user or (
            row.role is MsgRole.assistant and row.tool_calls is None and (row.content or "").strip()
        ):
            keep.append(row)
    return keep


# ---------- FAQ ----------


def find_faq(session: Session, *, keyword: str, limit: int = 5) -> list[Faq]:
    """关键词检索 —— 三个字段的 OR LIKE。

    这是**刻意**的关键词匹配,不是检索:命中不到就是命中不到。验收③
    「邮费是多少」查不到政策原文,是设计出来的漏召回,留给 ch03 做向量检索。
    在这里补同义词/模糊匹配会把那个基准测试悄悄改掉。
    """
    pattern = f"%{_escape_like(keyword)}%"
    return (
        session.query(Faq)
        .filter(
            or_(
                Faq.question.like(pattern, escape=_LIKE_ESCAPE),
                Faq.answer.like(pattern, escape=_LIKE_ESCAPE),
                Faq.category.like(pattern, escape=_LIKE_ESCAPE),
            )
        )
        .order_by(Faq.id)
        .limit(limit)
        .all()
    )


# ---------- 工单 ----------


def next_ticket_no(session: Session, *, day: dt.date) -> str:
    """当天下一个工单号:`T{yyyymmdd}{三位序号}`。

    用「查当天已有条数 +1」而不是自增序列:工单号要人念得出来,也让人一眼看出
    是哪天的。并发下两个请求可能算出同一个号 —— 这是已知的,主键冲突由
    `insert_ticket` 抛 IntegrityError,调用方换号重试(本章单机低并发,不为此加锁)。
    """
    prefix = f"T{day:%Y%m%d}"
    used = (
        session.query(func.count(Ticket.ticket_no))
        .filter(Ticket.ticket_no.like(prefix + "%", escape=_LIKE_ESCAPE))
        .scalar()
        or 0
    )
    return f"{prefix}{used + 1:03d}"


def insert_ticket(
    session: Session,
    *,
    conversation_id: int,
    description: str,
    ticket_type: TicketType,
    ticket_no: str,
    request_id: str | None = None,
) -> None:
    """建工单。`status` 不显式给 —— 走 DDL 的 `DEFAULT '待处理'`。

    撞号时 IntegrityError 直接抛出去,由调用方(工具)决定换号重试还是如实告知用户。
    这里吞掉的话,用户会拿到一个"已建单"的回复而库里根本没有这张单。
    """
    session.add(
        Ticket(
            ticket_no=ticket_no,
            request_id=request_id,
            conversation_id=conversation_id,
            description=description,
            ticket_type=ticket_type,
        )
    )


def find_ticket_by_request_id(session: Session, *, request_id: str) -> Ticket | None:
    return session.query(Ticket).filter(Ticket.request_id == request_id).one_or_none()
