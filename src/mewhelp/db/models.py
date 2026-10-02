"""四张表的 ORM 映射 —— 对 `sql/ch02-ddl.sql` 的镜像。

`sql/ch02-ddl.sql` 才是权威(MySQL 侧由它建表)。这个文件的作用是:
1. 让 SQLite 测试能建出同构的表;
2. 让 `tests/test_db_ddl_drift.py` 能把两者钉在一起,防止漂移。
"""

import datetime as dt
import enum

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeEngine

from .base import Base

# MySQL 要 BIGINT UNSIGNED,SQLite 要类型名恰为 INTEGER(否则不被当 rowid 别名,
# 自增失效)。两边都要,所以用 variant 抹平 —— 实测 id 拿到 1、2。
BIGINT_PK: TypeEngine = mysql.BIGINT(unsigned=True).with_variant(Integer, "sqlite")

# MySQL 是原生 JSON,SQLite 落成 TEXT。语义差异见 spec §16 风险 7:
# 本章只做整体读写、不做 JSON 路径查询,所以差异不显现。
JSON_COLUMN = JSON().with_variant(mysql.JSON(), "mysql")


def _chinese_enum(enum_cls) -> Enum:
    """存**值**(中文)而不是成员名,并且让 SQLite 也真的拒非法值。

    **第一件事** —— SQLAlchemy 的 Enum 默认序列化 `enum_cls.ongoing` 的**成员名**,
    库里会变成 "ConvStatus.ongoing"。必须显式给 values_callable。这是本项目第二次
    踩同一类坑(ch01 的 `f"{intent}"` 得到 `AfterSalesIntent.refund`)。

    **第二件事** —— `create_constraint` 自 SQLAlchemy 1.4 起默认是 **False**,不打开
    的话 SQLite 侧只有一列裸 `VARCHAR`,写 `role="system"` 照收不误。spec §17 把
    「ENUM 在 SQLite 上落成 CHECK 并真的拒非法值」列为 `test_db_models.py` 的交付项,
    所以这里显式打开。实测:SQLite 侧产出 `CHECK (role IN ('user','assistant','tool'))`;
    MySQL 侧**一字未变**(原生 ENUM 已经承担了约束,SQLAlchemy 不再叠 CHECK)——
    所以这条不影响漂移测试对 MySQL 方言的断言。
    """
    return Enum(
        enum_cls,
        values_callable=lambda e: [m.value for m in e],
        create_constraint=True,
    )


class ConvStatus(enum.Enum):
    ongoing = "进行中"
    human = "已转人工"
    # 本章没有任何路径写入 closed —— 如实留着,不假装有闭环。
    closed = "已结束"


class MsgRole(enum.Enum):
    user = "user"
    assistant = "assistant"
    tool = "tool"


class TicketType(enum.Enum):
    after_sales = "售后"
    complaint = "投诉"
    consult = "咨询"


class TicketStatus(enum.Enum):
    pending = "待处理"
    done = "已处理"


class Conversation(Base):
    __tablename__ = "conversations"
    __table_args__ = (
        Index("idx_user_id", "user_id"),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_comment": "客服会话",
        },
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    # 全章唯一一处偏离用户 DDL 的列。理由见 spec §6.3:自增 id 可枚举,猜中即别人的会话。
    session_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ConvStatus] = mapped_column(
        _chinese_enum(ConvStatus), nullable=False, server_default=text("'进行中'")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    # ORM 产不出 ON UPDATE CURRENT_TIMESTAMP(实测 server_onupdate 不落 DDL),
    # 所以 MySQL 侧这个行为只存在于 sql/ch02-ddl.sql。SQLite 侧不更新,
    # 而本章没有任何逻辑读 updated_at。
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("idx_conversation_id", "conversation_id"),
        Index("uk_messages_ch06_event_key", "ch06_event_key", unique=True),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        BIGINT_PK,
        ForeignKey("conversations.id", name="fk_messages_conversation"),
        nullable=False,
    )
    role: Mapped[MsgRole] = mapped_column(_chinese_enum(MsgRole), nullable=False)
    # 可空:assistant 纯工具调用那一轮没有正文。
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_calls: Mapped[list | None] = mapped_column(JSON_COLUMN, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    citations: Mapped[list[dict] | None] = mapped_column(JSON_COLUMN, nullable=True)
    ch06_event_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Faq(Base):
    __tablename__ = "faq"
    __table_args__ = (
        Index("idx_category", "category"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(String(512), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class Ticket(Base):
    __tablename__ = "tickets"
    __table_args__ = (
        # 与 messages 的索引**故意重名** —— 权威 DDL 里两张表都叫 idx_conversation_id,
        # MySQL 的索引名只在表内唯一,所以那是合法的。SQLite 不是:它的索引名在
        # **整个库**里唯一,第二条 CREATE INDEX 会撞名报 "index ... already exists"。
        #
        # `ddl_if(dialect="mysql")` 让这条只在 MySQL 侧产出:SQLite 建表时跳过它
        # (测试不依赖任何索引),而 MySQL 方言下编译出的语句与 DDL 逐字一致。
        # Index 对象本身仍留在 table.indexes 里,所以漂移测试照样能断言这个名字。
        Index("idx_conversation_id", "conversation_id").ddl_if(dialect="mysql"),
        Index("uk_tickets_request_id", "request_id", unique=True),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )

    # 工单号即主键,不是自增代理键 —— 业务上一个工单就该有一个人给的号。
    ticket_no: Mapped[str] = mapped_column(String(32), primary_key=True)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    conversation_id: Mapped[int] = mapped_column(
        BIGINT_PK,
        ForeignKey("conversations.id", name="fk_tickets_conversation"),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    ticket_type: Mapped[TicketType] = mapped_column(_chinese_enum(TicketType), nullable=False)
    status: Mapped[TicketStatus] = mapped_column(
        _chinese_enum(TicketStatus), nullable=False, server_default=text("'待处理'")
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class RefundApplication(Base):
    __tablename__ = "refund_applications"
    __table_args__ = (
        Index("uk_refund_applications_offer_id", "offer_id", unique=True),
        Index("uk_refund_applications_application_no", "application_no", unique=True),
        Index("idx_refund_applications_conversation", "conversation_id"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )
    id: Mapped[int] = mapped_column(BIGINT_PK, primary_key=True, autoincrement=True)
    application_no: Mapped[str] = mapped_column(String(32), nullable=False)
    offer_id: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_id: Mapped[int] = mapped_column(BIGINT_PK,
        ForeignKey("conversations.id", name="fk_refund_applications_conversation"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    order_snapshot: Mapped[dict] = mapped_column(JSON_COLUMN, nullable=False)
    assessment_snapshot: Mapped[dict] = mapped_column(JSON_COLUMN, nullable=False)
    policy_snapshot: Mapped[list] = mapped_column(JSON_COLUMN, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False,
                                                server_default=text("CURRENT_TIMESTAMP"))
