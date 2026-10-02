"""Apply only validated additive Ch06 changes after a consistent database backup."""

import re

from sqlalchemy import Engine, String, inspect

from mewhelp.db.models import RefundApplication


def _validate_refunds(engine):
    inspector = inspect(engine)
    if "refund_applications" not in inspector.get_table_names():
        return False
    actual = {c["name"]: c for c in inspector.get_columns("refund_applications")}
    table = RefundApplication.__table__
    if set(actual) != set(table.columns.keys()):
        raise RuntimeError("Incompatible refund_applications columns")
    if inspector.get_pk_constraint(table.name)["constrained_columns"] != ["id"]:
        raise RuntimeError("Incompatible refund_applications primary key")
    for col in table.columns:
        got = actual[col.name]
        want_type = col.type.compile(dialect=engine.dialect).upper()
        got_type = got["type"].compile(dialect=engine.dialect).upper()
        if col.name in {"id", "conversation_id"}:
            got_type = re.sub(r"(BIGINT|INTEGER)\(\d+\)", r"\1", got_type)
        if got_type != want_type or got["nullable"] != col.nullable:
            raise RuntimeError(f"Incompatible refund_applications.{col.name}")
        if col.server_default is not None:
            default = str(got.get("default") or "").strip("'\"() ").replace("()", "").upper()
            wanted = str(col.server_default.arg).strip("'\"() ").replace("()", "").upper()
            if default != wanted:
                raise RuntimeError(f"Incompatible refund_applications.{col.name} default")
    if engine.dialect.name == "mysql" and actual["id"].get("autoincrement") is not True:
        raise RuntimeError("Incompatible refund_applications.id AUTO_INCREMENT")
    indexes = {i["name"]: i for i in inspector.get_indexes(table.name)}
    for index in table.indexes:
        got = indexes.get(index.name)
        if (not got or got["column_names"] != [c.name for c in index.columns]
            or bool(got["unique"]) != index.unique):
            raise RuntimeError(f"Incompatible refund_applications index {index.name}")
    fks = inspector.get_foreign_keys(table.name)
    if not any(f["constrained_columns"] == ["conversation_id"] and f["referred_table"] == "conversations"
               and f["referred_columns"] == ["id"] for f in fks):
        raise RuntimeError("Incompatible refund_applications foreign key")
    return True


def _inspect(engine):
    inspector = inspect(engine)
    if "messages" not in inspector.get_table_names():
        raise RuntimeError("Create Ch02 schema before Ch06 migration")
    columns = {c["name"]: c for c in inspector.get_columns("messages")}
    has_column = "ch06_event_key" in columns
    if has_column:
        col = columns["ch06_event_key"]
        if not (isinstance(col["type"], String) and col["type"].length == 64 and col["nullable"]):
            raise RuntimeError("Incompatible messages.ch06_event_key")
    key = next((i for i in inspector.get_indexes("messages") if i["name"] == "uk_messages_ch06_event_key"), None)
    if key is not None and not (key["unique"] and key["column_names"] == ["ch06_event_key"]):
        raise RuntimeError("Incompatible uk_messages_ch06_event_key")
    return has_column, key is not None


def migrate_ch06(engine: Engine):
    if engine.dialect.name not in {"sqlite", "mysql"}:
        raise RuntimeError(f"Unsupported migration dialect: {engine.dialect.name}")
    has_column, has_key = _inspect(engine)
    has_refunds = _validate_refunds(engine)
    with engine.begin() as conn:
        if not has_column:
            conn.exec_driver_sql("ALTER TABLE messages ADD COLUMN ch06_event_key VARCHAR(64) NULL")
        if not has_key:
            conn.exec_driver_sql("CREATE UNIQUE INDEX uk_messages_ch06_event_key ON messages (ch06_event_key)")
        if not has_refunds:
            RefundApplication.__table__.create(conn)
    _inspect(engine)
    _validate_refunds(engine)


if __name__ == "__main__":
    from mewhelp.db.engine import engine
    migrate_ch06(engine)
    print("Ch06 nullable message key verified; additive migration complete.")
