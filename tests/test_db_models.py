"""ORM 映射 —— SQLite 内存库,不需要 Docker。"""


import pytest
from sqlalchemy import create_engine, select
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
from mewhelp.knowledge import store as knowledge_store  # noqa: F401 — 注册 ch03 表到 Base
from mewhelp.knowledge.refusals import LowConfidenceQuestion  # noqa: F401


@pytest.fixture
def engine():
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


def test_tables_are_created(engine):
    from sqlalchemy import inspect

    assert set(inspect(engine).get_table_names()) == {
        "conversations",
        "conversation_summaries",
        "messages",
        "faq",
        "tickets",
        "knowledge_chunks",
        "qa_extraction_staging",
        "low_confidence_questions",
        "refund_applications",
        "tool_audit_logs",
        "review_queue",
        "eval_runs",
    }


def test_bigint_pk_autoincrements_on_sqlite(session):
    """BIGINT 主键在 SQLite 上不自增 —— SQLite 只把类型名恰为 INTEGER 的列当 rowid 别名。

    这条守的是 BIGINT_PK 那个 with_variant。把 with_variant 去掉,插入拿到的是
    id=None(或撞主键),而不是 1、2。
    """
    session.add_all([
        Conversation(session_id="s1", user_id="u1"),
        Conversation(session_id="s2", user_id="u1"),
    ])
    session.commit()

    ids = session.scalars(select(Conversation.id).order_by(Conversation.id)).all()
    assert ids == [1, 2]


def test_chinese_enum_stores_the_value_not_the_member_name(session):
    """Enum 默认序列化成员名。不写 values_callable 的话库里是 ConvStatus.ongoing。

    与 ch01 撞过的 `f"{intent}"` 得到 `AfterSalesIntent.refund` 是同一类坑:
    中文值不是自然得到的,是 values_callable 挣来的。
    """
    session.add(Conversation(session_id="s1", user_id="u1"))
    session.commit()

    raw = session.connection().exec_driver_sql(
        "select status from conversations"
    ).scalar()
    assert raw == "进行中"


def test_status_default_is_applied_when_not_given(session):
    """server_default 要在**不传**这一列时生效。"""
    session.add(Conversation(session_id="s1", user_id="u1"))
    session.commit()

    conv = session.scalars(select(Conversation)).one()
    assert conv.status is ConvStatus.ongoing


@pytest.mark.parametrize(
    "role",
    [
        pytest.param("user", id="user"),
        pytest.param("assistant", id="assistant"),
        pytest.param("tool", id="tool"),
    ],
)
def test_role_accepts_the_three_legal_values(session, role):
    session.add(Conversation(session_id="s1", user_id="u1"))
    session.flush()
    session.add(Message(conversation_id=1, role=MsgRole(role)))
    session.commit()


def test_role_rejects_an_illegal_value(session):
    """ENUM 在 SQLite 上落成 VARCHAR + CHECK,所以非法值由**库**挡,不只靠应用层约定。"""
    session.add(Conversation(session_id="s1", user_id="u1"))
    session.flush()
    session.add(Message(conversation_id=1, role="system"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_unknown_conversation_id_is_rejected_by_the_foreign_key(session):
    """messages.conversation_id 上有真外键,不是应用层约定。

    注意:SQLite 默认**不**强制外键,要靠 PRAGMA foreign_keys=ON。
    这条同时守住了「Base 或 engine 有没有把那条 PRAGMA 打开」。
    """
    session.add(Message(conversation_id=999, role=MsgRole.user, content="孤儿"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_session_id_is_unique(session):
    session.add_all([
        Conversation(session_id="dup", user_id="u1"),
        Conversation(session_id="dup", user_id="u2"),
    ])
    with pytest.raises(IntegrityError):
        session.commit()


def test_tool_calls_json_round_trips(session):
    """tool_calls 是 JSON 列。SQLite 存成 TEXT,MySQL 是原生 JSON —— 本章只做整体读写。"""
    session.add(Conversation(session_id="s1", user_id="u1"))
    session.flush()
    calls = [{"name": "query_logistics", "args": {"order_id": "1001"}, "id": "call_1"}]
    session.add(
        Message(conversation_id=1, role=MsgRole.assistant, content=None, tool_calls=calls)
    )
    session.commit()
    session.expire_all()

    msg = session.scalars(select(Message)).one()
    assert msg.tool_calls == calls
    assert msg.content is None


def test_ticket_no_is_the_primary_key_and_enum_values_are_chinese(session):
    session.add(Conversation(session_id="s1", user_id="u1"))
    session.flush()
    session.add(
        Ticket(
            ticket_no="T20260928001",
            conversation_id=1,
            description="鞋子开胶",
            ticket_type=TicketType.after_sales,
        )
    )
    session.commit()

    raw = session.connection().exec_driver_sql("select ticket_type from tickets").scalar()
    assert raw == "售后"
    assert session.scalars(select(Ticket)).one().status is TicketStatus.pending


def test_faq_question_is_512(session):
    """DDL 写的是 VARCHAR(512)。SQLite 不强制长度,所以断言的是 model 属性。"""
    session.add(Faq(question="q" * 512, answer="a", category="退换货"))
    session.commit()
    assert Faq.__table__.c.question.type.length == 512


def test_updated_at_columns_exist(session):
    """DDL 里 conversations 与 faq 各有 updated_at。ORM 产不出 ON UPDATE,但列要在。"""
    assert "updated_at" in Conversation.__table__.c
    assert "updated_at" in Faq.__table__.c
