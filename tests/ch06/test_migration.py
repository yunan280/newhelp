from importlib import import_module

import pytest
from sqlalchemy import create_engine, inspect


def migrate(engine):
    try:
        return import_module("scripts.migrate_ch06_schema").migrate_ch06(engine)
    except ImportError:
        pytest.fail("missing additive Ch06 migration")


def test_message_upgrade_twice_preserves_null_legacy_rows():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE conversations (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE messages (id INTEGER PRIMARY KEY, content TEXT)")
        conn.exec_driver_sql("INSERT INTO messages VALUES (1,'旧消息')")
    migrate(engine); migrate(engine)
    with engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT content,ch06_event_key FROM messages").one() == ("旧消息", None)
    indexes = inspect(engine).get_indexes("messages")
    assert any(i["name"] == "uk_messages_ch06_event_key" and i["unique"] for i in indexes)
    assert "refund_applications" in inspect(engine).get_table_names()


@pytest.mark.parametrize("column,index", [
    ("ch06_event_key VARCHAR(32) NULL", ""),
    ("ch06_event_key VARCHAR(64) NOT NULL", ""),
    ("ch06_event_key VARCHAR(64) NULL", "CREATE INDEX uk_messages_ch06_event_key ON messages(ch06_event_key)"),
])
def test_incompatible_existing_message_key_rejected(column, index):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql(f"CREATE TABLE messages (id INTEGER PRIMARY KEY, {column})")
        if index:
            conn.exec_driver_sql(index)
    with pytest.raises(RuntimeError, match="Incompatible"):
        migrate(engine)


@pytest.mark.parametrize("defect", ["column_length", "extra_column", "nonunique_offer", "wrong_status"])
def test_existing_refund_schema_must_match_exact_contract(defect):
    from sqlalchemy import Column, MetaData, String, text

    from mewhelp.db.models import Conversation, Message, RefundApplication
    metadata = MetaData()
    Conversation.__table__.to_metadata(metadata)
    Message.__table__.to_metadata(metadata)
    refund = RefundApplication.__table__.to_metadata(metadata)
    if defect == "column_length":
        refund.c.offer_id.type = String(32)
    elif defect == "extra_column":
        refund.append_column(Column("unexpected", String(16)))
    elif defect == "wrong_status":
        from sqlalchemy import DefaultClause
        refund.c.status.server_default = DefaultClause(text("'approved'"))
    else:
        next(i for i in refund.indexes if i.name == "uk_refund_applications_offer_id").unique = False
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with pytest.raises(RuntimeError, match="Incompatible"):
        migrate(engine)
