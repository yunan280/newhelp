"""create_ticket —— 真写 tickets 表,并把会话置「已转人工」。"""

import datetime as dt

import pytest
from langchain_core.utils.function_calling import convert_to_openai_tool
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mewhelp.db.base import Base
from mewhelp.db.models import Conversation, ConvStatus, Ticket, TicketType
from mewhelp.db.seed import seed
from mewhelp.tools.ticket import build_registry, build_ticket_tools


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        seed(s)
        s.add(Conversation(session_id="s1", user_id="u1"))
        s.commit()
    return lambda: Session(engine)


@pytest.fixture
def create_ticket(session_factory):
    return {t.name: t for t in build_ticket_tools(session_factory, conversation_id=1)}[
        "create_ticket"
    ]


async def test_writes_a_ticket_and_returns_the_no(create_ticket, session_factory):
    out = await create_ticket.ainvoke({"description": "鞋子开胶了", "ticket_type": "售后"})

    assert out.strip()
    with session_factory() as s:
        ticket = s.scalars(select(Ticket)).one()
    assert ticket.description == "鞋子开胶了"
    assert ticket.ticket_type is TicketType.after_sales
    # 工单号要出现在回灌给模型的内容里,否则模型没法告诉用户单号
    assert ticket.ticket_no in out


async def test_ticket_no_follows_the_documented_format(create_ticket, session_factory):
    await create_ticket.ainvoke({"description": "x", "ticket_type": "咨询"})
    with session_factory() as s:
        ticket_no = s.scalars(select(Ticket.ticket_no)).one()
    assert ticket_no.startswith(f"T{dt.date.today():%Y%m%d}")  # noqa: DTZ011 — 工单编号沿用本地日期
    assert len(ticket_no) == len("T20260928001")


async def test_ticket_creation_preserves_conversation_status(create_ticket, session_factory):
    """建单和转人工是独立动作；工具不能暗中修改会话状态。"""
    out = await create_ticket.ainvoke({"description": "要投诉", "ticket_type": "投诉"})

    with session_factory() as s:
        conv = s.scalars(select(Conversation)).one()
        assert len(s.scalars(select(Ticket)).all()) == 1
    assert conv.status is ConvStatus.ongoing
    assert "已同步转交人工" not in out


@pytest.mark.parametrize("ticket_type", ["售后", "投诉", "咨询"])
async def test_accepts_exactly_the_three_ddl_values(create_ticket, session_factory, ticket_type):
    out = await create_ticket.ainvoke({"description": "x", "ticket_type": ticket_type})
    assert out.strip()
    with session_factory() as s:
        assert s.scalars(select(Ticket)).one().ticket_type.value == ticket_type


async def test_rejects_a_ticket_type_outside_the_three(create_ticket):
    """Literal 挡在**工具**这一层,由执行管线翻成结构化失败。

    模型乱填类型是很常见的(比如填「退款」)。挡不住的话会一路写到 ENUM 列上,
    由数据库抛 IntegrityError —— 那是个 500,而不是一条模型能读懂的回灌。
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await create_ticket.ainvoke({"description": "x", "ticket_type": "退款"})


async def test_conversation_id_is_injected_not_exposed(create_ticket):
    """闭包注入的**核心断言**:conversation_id 不在签名里,模型不可能编它。

    这是 InjectedToolArg 的替代方案。实测那个装饰器在 1.6.5 上不生效 ——
    被标注的参数照样进 properties 与 required。这里断的是它根本没出现。
    """
    schema = convert_to_openai_tool(create_ticket)
    props = set(schema["function"]["parameters"]["properties"])
    assert props == {"description", "ticket_type"}
    assert "conversation_id" not in props


async def test_conversation_id_lands_in_the_right_row(session_factory):
    """两个会话各有各的工单,不能串。"""
    with session_factory() as s:
        s.add(Conversation(session_id="s2", user_id="u1"))
        s.commit()

    first = build_ticket_tools(session_factory, conversation_id=1)[0]
    second = build_ticket_tools(session_factory, conversation_id=2)[0]
    await first.ainvoke({"description": "会话一的单", "ticket_type": "咨询"})
    await second.ainvoke({"description": "会话二的单", "ticket_type": "投诉"})

    with session_factory() as s:
        rows = {t.conversation_id: t.description for t in s.scalars(select(Ticket))}
    assert rows == {1: "会话一的单", 2: "会话二的单"}


async def test_gives_up_gracefully_and_rolls_back_when_the_number_keeps_colliding(session_factory):
    """连续撞号的兜底分支(计划里没有这条):回灌一句失败,**不抛**。

    `next_ticket_no` 是"数当天已有几条 +1",所以换号重试只在**真并发**里有用
    (对手提交后 count 涨了,重算就拿到新号)。本测试造不出那个竞态,改成一个
    **号段有空缺**的局面:表里已有 001 与 003,于是每次都算出 003、每次都撞 ——
    正好走满 5 次进兜底分支。它验的不是"重试有用",而是"重试耗尽时不会抛 500"。

    同时钉住失败的**后果**:没有多写一条工单,会话也没被标成「已转人工」——
    后者才是真正难看的形态:用户被告知已转人工,人工那边却查无此单。
    (实测这一步靠的是 `with session` 退出时的隐式回滚,`ticket.py` 里那句显式
    `rollback()` 并非它的必要条件 —— 别把功劳记错,注释里已写明。)
    """
    today = dt.date.today()  # noqa: DTZ011 — 与工单编号的本地日期一致
    stamp = f"T{today:%Y%m%d}"
    with session_factory() as s:
        s.add(
            Ticket(
                ticket_no=f"{stamp}001",
                conversation_id=1,
                description="占位",
                ticket_type=TicketType.consult,
            )
        )
        s.add(
            Ticket(
                ticket_no=f"{stamp}003",
                conversation_id=1,
                description="占位",
                ticket_type=TicketType.consult,
            )
        )
        s.commit()

    tool = build_ticket_tools(session_factory, conversation_id=1)[0]
    out = await tool.ainvoke({"description": "x", "ticket_type": "咨询"})

    assert "失败" in out and "冲突" in out
    with session_factory() as s:
        assert s.query(Ticket).count() == 2  # 没有多写一条
        assert s.scalars(select(Conversation)).one().status is ConvStatus.ongoing


# ---------- build_registry ----------


def test_registry_holds_all_five_tools(session_factory):
    registry = build_registry(session_factory, conversation_id=1)
    assert set(registry.names()) == {
        "query_order",
        "query_product",
        "query_logistics",
        "query_faq",
        "create_ticket",
    }


def test_create_ticket_is_marked_not_retryable(session_factory):
    """写类工具没有幂等设施 —— 重试会重复建单。

    用户投诉一次、工单出来两张,是这个洞的形态。普通读工具仍可重试；
    FAQ 读工具因为大模型冷加载且超时线程不会取消，单次执行给更长时限。
    """
    registry = build_registry(session_factory, conversation_id=1)

    assert registry.get("create_ticket").retryable is False
    for name in ("query_order", "query_product", "query_logistics"):
        assert registry.get(name).retryable is True
    assert registry.get("query_faq").retryable is False
    # 本机 Docker 与模型同时冷启动时 FAQ 曾耗时 58.6 秒，需留出明显余量。
    assert registry.get("query_faq").timeout_seconds >= 90.0


def test_registry_tools_are_all_bindable(session_factory):
    """5 个工具都要能进 bind_tools —— 有一个不合规,bind 会在真机上炸。"""
    registry = build_registry(session_factory, conversation_id=1)
    for t in registry.tools():
        assert "function" in convert_to_openai_tool(t)


async def test_no_tool_exposes_context_parameters(session_factory):
    """5 个工具**无一**把上下文参数暴露给模型 —— 这是本章的架构不变式。"""
    registry = build_registry(session_factory, conversation_id=1)
    forbidden = {"db", "session", "session_id", "conversation_id", "session_factory"}
    for t in registry.tools():
        props = set(convert_to_openai_tool(t)["function"]["parameters"]["properties"])
        assert not (props & forbidden), f"{t.name} 暴露了上下文参数:{props & forbidden}"
