"""会话 / 消息 / FAQ / 工单的读写 —— SQLite 内存库。"""

import datetime as dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from mewhelp.db.base import Base
from mewhelp.db.models import (
    Conversation,
    ConvStatus,
    Faq,
    Message,
    MsgRole,
    Ticket,
    TicketStatus,
    TicketType,
)
from mewhelp.db.repository import (
    TurnMessage,
    append_messages,
    find_faq,
    get_or_create_conversation,
    insert_ticket,
    load_replay_messages,
    next_ticket_no,
    set_conversation_status,
)


@pytest.fixture
def session():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


# ---------- 会话身份的三态(spec §12.3) ----------


def test_creates_a_conversation_when_there_is_none(session):
    conv, created = get_or_create_conversation(session, session_id="s1", user_id="u1")
    session.commit()

    assert created is True
    assert conv.session_id == "s1"
    assert conv.status is ConvStatus.ongoing


def test_returns_the_existing_conversation_and_marks_not_created(session):
    first, _ = get_or_create_conversation(session, session_id="s1", user_id="u1")
    session.commit()

    second, created = get_or_create_conversation(session, session_id="s1", user_id="u1")

    assert created is False
    assert second.id == first.id


def test_a_new_user_id_on_an_existing_session_does_not_overwrite_the_ledger(session):
    """Review Focus #5:同一 session 换了 user_id,**保留首轮那个**。

    这是"不静默改写账本",与鉴权无关 —— 被改掉的话,同一段对话的归属会在中途换人,
    而历史消息还挂在原来那个人名下。spec §6.4 明确承诺了不覆盖。
    """
    get_or_create_conversation(session, session_id="s1", user_id="u1")
    session.commit()

    conv, _ = get_or_create_conversation(session, session_id="s1", user_id="u2")
    session.commit()
    session.expire_all()

    assert conv.user_id == "u1"
    assert session.query(Conversation).one().user_id == "u1"


def test_concurrent_creation_of_the_same_session_raises_integrity_error(session):
    """并发建同一个 session_id 会撞 UNIQUE —— 调用方据此重查(见 service 层)。

    这条断的是"确实会抛",不然 service 里那个 except 是死代码。
    """
    session.add(Conversation(session_id="dup", user_id="u1"))
    session.commit()

    session.add(Conversation(session_id="dup", user_id="u2"))
    with pytest.raises(IntegrityError):
        session.commit()


# ---------- 消息落库 ----------


def _conv(session) -> int:
    conv, _ = get_or_create_conversation(session, session_id="s1", user_id="u1")
    session.flush()
    return conv.id


def test_appends_a_four_row_tool_turn_in_order(session):
    cid = _conv(session)
    append_messages(session, conversation_id=cid, rows=[
        TurnMessage(role=MsgRole.user, content="订单 1001 的物流到哪了"),
        TurnMessage(role=MsgRole.assistant, content=None,
                    tool_calls=[{"name": "query_logistics", "args": {"order_id": "1001"}, "id": "c1"}]),
        TurnMessage(role=MsgRole.tool, content="顺丰 已到杭州", tool_call_id="c1"),
        TurnMessage(role=MsgRole.assistant, content="您的包裹已到杭州。"),
    ])
    session.commit()

    rows = session.query(Message).order_by(Message.id).all()
    assert [r.role for r in rows] == [
        MsgRole.user, MsgRole.assistant, MsgRole.tool, MsgRole.assistant
    ]
    assert rows[1].content is None
    assert rows[2].tool_call_id == "c1"


def test_appends_two_rows_for_a_plain_turn(session):
    cid = _conv(session)
    append_messages(session, conversation_id=cid, rows=[
        TurnMessage(role=MsgRole.user, content="你好"),
        TurnMessage(role=MsgRole.assistant, content="您好,有什么可以帮您?"),
    ])
    session.commit()

    assert session.query(Message).count() == 2


def test_appending_an_empty_row_list_writes_nothing(session):
    cid = _conv(session)
    append_messages(session, conversation_id=cid, rows=[])
    session.commit()

    assert session.query(Message).count() == 0


# ---------- 跨轮回放过滤(spec §11) ----------


def test_replay_keeps_only_user_and_final_assistant(session):
    """带 tool_calls 的 assistant(常夹带 preamble)与 tool 消息**不**跨轮回放。

    回放它们会让模型看到自己上一轮的半截前言,续接时的语气与内容都会被带偏。
    但全部消息仍完整落库 —— 这条同时断言了"库里是 4 条、回放只给 2 条"。
    """
    cid = _conv(session)
    append_messages(session, conversation_id=cid, rows=[
        TurnMessage(role=MsgRole.user, content="订单 1001 的物流"),
        TurnMessage(role=MsgRole.assistant, content="让我查一下",
                    tool_calls=[{"name": "query_logistics", "args": {}, "id": "c1"}]),
        TurnMessage(role=MsgRole.tool, content="已到杭州", tool_call_id="c1"),
        TurnMessage(role=MsgRole.assistant, content="您的包裹已到杭州。"),
    ])
    session.commit()

    assert session.query(Message).count() == 4

    replayed = load_replay_messages(session, conversation_id=cid)
    assert [(m.role, m.content) for m in replayed] == [
        (MsgRole.user, "订单 1001 的物流"),
        (MsgRole.assistant, "您的包裹已到杭州。"),
    ]


def test_replay_drops_an_assistant_row_with_empty_content(session):
    """content 为空的 assistant 行也不是"最终回答",不该回放。

    工具调用轮那一行 content 是 None;若它同时没有 tool_calls(理论上不该发生),
    回放一条空白的 assistant 消息会污染上下文。
    """
    cid = _conv(session)
    append_messages(session, conversation_id=cid, rows=[
        TurnMessage(role=MsgRole.user, content="在吗"),
        TurnMessage(role=MsgRole.assistant, content="   "),
    ])
    session.commit()

    assert [m.content for m in load_replay_messages(session, conversation_id=cid)] == ["在吗"]


def test_replay_is_scoped_to_one_conversation(session):
    first_id = _conv(session)
    second, _ = get_or_create_conversation(session, session_id="s2", user_id="u1")
    session.flush()

    append_messages(session, conversation_id=first_id, rows=[
        TurnMessage(role=MsgRole.user, content="会话一的提问"),
    ])
    append_messages(session, conversation_id=second.id, rows=[
        TurnMessage(role=MsgRole.user, content="会话二的提问"),
    ])
    session.commit()

    assert [m.content for m in load_replay_messages(session, conversation_id=second.id)] == [
        "会话二的提问"
    ]


def test_replay_returns_messages_in_insertion_order(session):
    cid = _conv(session)
    append_messages(session, conversation_id=cid, rows=[
        TurnMessage(role=MsgRole.user, content="第一问"),
        TurnMessage(role=MsgRole.assistant, content="第一答"),
    ])
    append_messages(session, conversation_id=cid, rows=[
        TurnMessage(role=MsgRole.user, content="第二问"),
        TurnMessage(role=MsgRole.assistant, content="第二答"),
    ])
    session.commit()
    session.expire_all()

    assert [m.content for m in load_replay_messages(session, conversation_id=cid)] == [
        "第一问", "第一答", "第二问", "第二答"
    ]


# ---------- FAQ 检索 ----------


@pytest.fixture
def faq_session(session):
    session.add_all([
        Faq(question="退货政策是什么", answer="签收后 7 天内可无理由退货。", category="退换货"),
        Faq(question="运费怎么计算", answer="满 99 元包邮,否则 8 元。", category="物流"),
        Faq(question="发票怎么开", answer="下单时勾选即可。", category="发票"),
    ])
    session.commit()
    return session


def test_finds_by_substring(faq_session):
    rows = find_faq(faq_session, keyword="退货")
    assert [r.question for r in rows] == ["退货政策是什么"]


def test_searches_question_answer_and_category(faq_session):
    assert [r.question for r in find_faq(faq_session, keyword="99 元")] == ["运费怎么计算"]
    assert [r.question for r in find_faq(faq_session, keyword="发票")] == ["发票怎么开"]


def test_returns_empty_list_when_nothing_matches(faq_session):
    """「邮费」查不到 —— 这是验收③,刻意设计出来的漏召回(spec §8)。"""
    assert find_faq(faq_session, keyword="邮费") == []


@pytest.mark.parametrize(
    "keyword",
    [
        pytest.param("%", id="百分号"),
        pytest.param("_", id="下划线"),
        pytest.param("退货%", id="前缀加通配符"),
    ],
)
def test_like_wildcards_in_the_keyword_are_escaped(faq_session, keyword):
    """Review Focus #2:keyword 里的 % 与 _ 必须转义,否则 `LIKE '%%%'` 命中全部。

    这个洞是会真的开火的:模型抽关键词时完全可能给出 `%`,用户也可能直接问
    「% 是什么意思」。命中全部 12 条之后模型会拿着一堆不相关的政策编答案,
    而**没有任何报错**。`_` 更隐蔽 —— 它匹配任意单字符,几乎每条都中。
    """
    assert find_faq(faq_session, keyword=keyword) == []


def test_like_escape_does_not_break_normal_keywords(faq_session):
    """转义不能把正常关键词也转坏 —— 带下划线/百分号的合法问题是存在的。"""
    faq_session.add(Faq(question="折扣 50% 怎么算", answer="按原价打五折。", category="支付"))
    faq_session.commit()

    assert [r.question for r in find_faq(faq_session, keyword="50%")] == ["折扣 50% 怎么算"]


def test_respects_the_limit(faq_session):
    faq_session.add(Faq(question="发票可以重开吗", answer="可以。", category="发票"))
    faq_session.commit()

    assert len(find_faq(faq_session, keyword="发票", limit=1)) == 1


# ---------- 工单 ----------


def test_ticket_no_has_the_documented_format(session):
    assert next_ticket_no(session, day=dt.date(2026, 9, 28)) == "T20260928001"


def test_ticket_no_increments_within_the_same_day(session):
    cid = _conv(session)
    insert_ticket(session, conversation_id=cid, description="a",
                  ticket_type=TicketType.after_sales, ticket_no="T20260928001")
    session.commit()

    assert next_ticket_no(session, day=dt.date(2026, 9, 28)) == "T20260928002"


def test_ticket_no_restarts_on_a_new_day(session):
    cid = _conv(session)
    insert_ticket(session, conversation_id=cid, description="a",
                  ticket_type=TicketType.after_sales, ticket_no="T20260928001")
    session.commit()

    assert next_ticket_no(session, day=dt.date(2026, 9, 29)) == "T20260929001"


def test_insert_ticket_writes_chinese_enum_values(session):
    cid = _conv(session)
    insert_ticket(session, conversation_id=cid, description="鞋子开胶",
                  ticket_type=TicketType.complaint, ticket_no="T20260928001")
    session.commit()

    ticket = session.query(Ticket).one()
    assert ticket.ticket_type is TicketType.complaint
    assert ticket.status is TicketStatus.pending
    raw = session.connection().exec_driver_sql("select ticket_type from tickets").scalar()
    assert raw == "投诉"


def test_insert_ticket_raises_on_a_duplicate_ticket_no(session):
    """撞主键要抛出去 —— 调用方(工具)据此换号重试。"""
    cid = _conv(session)
    insert_ticket(session, conversation_id=cid, description="a",
                  ticket_type=TicketType.consult, ticket_no="T20260928001")
    session.commit()

    with pytest.raises(IntegrityError):
        insert_ticket(session, conversation_id=cid, description="b",
                      ticket_type=TicketType.consult, ticket_no="T20260928001")
        session.commit()


def test_set_conversation_status_moves_to_human(session):
    cid = _conv(session)
    set_conversation_status(session, conversation_id=cid, status=ConvStatus.human)
    session.commit()
    session.expire_all()

    assert session.query(Conversation).one().status is ConvStatus.human
