"""Apply only validated additive Ch06 changes after a consistent database backup."""

from sqlalchemy import Engine, String, inspect


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
    with engine.begin() as conn:
        if not has_column:
            conn.exec_driver_sql("ALTER TABLE messages ADD COLUMN ch06_event_key VARCHAR(64) NULL")
        if not has_key:
            conn.exec_driver_sql("CREATE UNIQUE INDEX uk_messages_ch06_event_key ON messages (ch06_event_key)")
    _inspect(engine)


if __name__ == "__main__":
    from mewhelp.db.engine import engine
    migrate_ch06(engine)
    print("Ch06 nullable message key verified; additive migration complete.")
