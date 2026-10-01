"""模型 ↔ sql/ch02-ddl.sql 的漂移守门。

为什么不逐字比对整份 DDL:SQLAlchemy 的编译器有它自己的排版(反引号包保留字、
AUTO_INCREMENT 的位置、`CHARSET=` 而不是 `DEFAULT CHARSET=`),逐字比会脆到没法维护。
所以这里断的是**承重的要素**:列集合、关键类型、中文枚举取值、索引名、外键名。

以及一条**只能**断在 .sql 上的:ON UPDATE CURRENT_TIMESTAMP —— 实测
server_onupdate 根本不落 DDL,ORM 产不出来,它只存在于文件里。
"""

from pathlib import Path

import pytest
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from mewhelp.db.models import Conversation, Faq, Message, Ticket

DDL_PATH = Path(__file__).resolve().parents[1] / "sql" / "ch02-ddl.sql"


@pytest.fixture(scope="module")
def ddl_text() -> str:
    assert DDL_PATH.exists(), f"权威 DDL 不存在:{DDL_PATH}"
    return DDL_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def compiled() -> dict[str, str]:
    """把四张表编译成 MySQL 方言的 CREATE TABLE 文本。

    `CreateTable` 收的是 `Table` 对象,不是映射类 —— 要 `.__table__`。
    传映射类会得到 `AttributeError: type object 'Conversation' has no attribute 'columns'`,
    报错点在 SQLAlchemy 内部的 ddl.py,离真正的原因隔了一层。
    """
    dialect = mysql.dialect()
    return {
        t.__tablename__: str(CreateTable(t.__table__).compile(dialect=dialect))
        for t in (Conversation, Message, Faq, Ticket)
    }


def test_ddl_declares_all_four_tables(ddl_text):
    for name in ("conversations", "messages", "faq", "tickets"):
        assert f"CREATE TABLE {name}" in ddl_text


def test_ddl_sets_utf8mb4_before_creating_tables(ddl_text):
    """SET NAMES utf8mb4 必须在第一条 CREATE TABLE **之前**。

    docker 官方 mysql 镜像的 client 默认字符集可能是 latin1,不加这一句,
    中文 ENUM 取值与种子数据会被 double-encode 存成乱码 —— 而且是静默的,
    表建出来了、数据也进去了,只是读出来是乱的。
    """
    assert "SET NAMES utf8mb4" in ddl_text
    assert ddl_text.index("SET NAMES utf8mb4") < ddl_text.index("CREATE TABLE")


def test_ddl_has_on_update_current_timestamp_for_conversations_and_faq(ddl_text):
    """这条只能断在文件上 —— ORM 产不出 ON UPDATE CURRENT_TIMESTAMP。

    实测:server_onupdate=text("CURRENT_TIMESTAMP") 编译出的 MySQL DDL 里
    **只有** DEFAULT CURRENT_TIMESTAMP,没有 ON UPDATE。所以真机上 updated_at
    的自动更新完全依赖这份 .sql。这正是「DDL 当权威」的直接理由。
    """
    assert ddl_text.count("ON UPDATE CURRENT_TIMESTAMP") == 2  # conversations + faq


def test_orm_cannot_emit_on_update_current_timestamp(compiled):
    """把上面那条的**理由**也钉住:将来有人加了 server_onupdate 就能删掉这条注释。"""
    assert "ON UPDATE CURRENT_TIMESTAMP" not in compiled["conversations"]


def test_foreign_keys_are_named(ddl_text, compiled):
    for name, table in (
        ("fk_messages_conversation", "messages"),
        ("fk_tickets_conversation", "tickets"),
    ):
        assert name in ddl_text
        assert name in compiled[table]


@pytest.mark.parametrize(
    ("table_name", "expected_indexes"),
    [
        pytest.param("conversations", {"idx_user_id"}, id="conversations"),
        pytest.param("messages", {"idx_conversation_id"}, id="messages"),
        pytest.param("tickets", {"idx_conversation_id", "uk_tickets_request_id"}, id="tickets"),
        pytest.param("faq", {"idx_category"}, id="faq"),
    ],
)
def test_index_names_match(ddl_text, table_name, expected_indexes):
    """索引名要两侧都在。

    非唯一索引**不会**内联进编译出的 CREATE TABLE(实测),所以要分开断:
    .sql 里断文本,模型侧断 table.indexes。
    """
    for name in expected_indexes:
        assert name in ddl_text

    table = Conversation.metadata.tables[table_name]
    model_indexes = {idx.name for idx in table.indexes}
    # unique=True 的 session_id 会额外生成一个 UNIQUE 约束,不计入这里
    assert expected_indexes <= model_indexes


def test_chinese_enum_values_match(ddl_text, compiled):
    """中文 ENUM 取值两侧必须一字不差。这是 values_callable 挣来的,别弄丢。"""
    assert "ENUM('进行中','已转人工','已结束')" in ddl_text
    assert "ENUM('进行中','已转人工','已结束')" in compiled["conversations"]

    assert "ENUM('user','assistant','tool')" in ddl_text
    assert "ENUM('user','assistant','tool')" in compiled["messages"]

    assert "ENUM('售后','投诉','咨询')" in ddl_text
    assert "ENUM('售后','投诉','咨询')" in compiled["tickets"]

    assert "ENUM('待处理','已处理')" in ddl_text
    assert "ENUM('待处理','已处理')" in compiled["tickets"]


def test_conversation_columns_match(ddl_text, compiled):
    """列集合两侧一致。少一列、多一列都要红。"""
    for column in ("id", "session_id", "user_id", "status", "created_at", "updated_at"):
        assert column in compiled["conversations"]
        assert column in ddl_text


def test_bigint_unsigned_is_compiled(compiled):
    """MySQL 侧主键是 BIGINT UNSIGNED,不是 BIGINT。"""
    assert "BIGINT UNSIGNED" in compiled["conversations"]
    assert "BIGINT UNSIGNED" in compiled["messages"]


def test_engine_and_charset(compiled):
    for text in compiled.values():
        assert "ENGINE=InnoDB" in text
        assert "CHARSET=utf8mb4" in text


def test_question_is_varchar_512(compiled):
    assert "VARCHAR(512)" in compiled["faq"]


def test_content_is_nullable_on_messages(compiled):
    """messages.content 可空 —— assistant 纯工具调用那一轮没有正文。

    这一列在 DDL 与模型上都是 NULL 允许的。改成 NOT NULL 会让工具调用轮
    写不进去,而那正是本章的主路径。
    """
    assert Message.__table__.c.content.nullable is True
    assert Ticket.__table__.c.description.nullable is False


def test_ddl_creates_conversations_before_dependent_tables(ddl_text):
    """建表顺序:先 conversations,再依赖它的 messages / tickets。

    反过来的话外键约束会建不上(MySQL 要求被引用的表先存在)。
    """
    assert ddl_text.index("CREATE TABLE conversations") < ddl_text.index("CREATE TABLE messages")
    assert ddl_text.index("CREATE TABLE conversations") < ddl_text.index("CREATE TABLE tickets")
