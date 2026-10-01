"""Add the nullable confirmation key after a consistent backup; safe to rerun."""

from sqlalchemy import Engine, String, inspect


def _inspect(engine: Engine) -> tuple[bool, bool]:
    inspector = inspect(engine)
    if "tickets" not in inspector.get_table_names():
        raise RuntimeError("Create Ch02 schema before Ch05 migration")
    columns = {c["name"]: c for c in inspector.get_columns("tickets")}
    has_column = "request_id" in columns
    if has_column:
        col = columns["request_id"]
        if not (isinstance(col["type"], String) and col["type"].length == 64 and col["nullable"]):
            raise RuntimeError("Incompatible tickets.request_id")
    indexes = {i["name"]: i for i in inspector.get_indexes("tickets")}
    key = indexes.get("uk_tickets_request_id")
    if key is not None and not (key["unique"] and key["column_names"] == ["request_id"]):
        raise RuntimeError("Incompatible uk_tickets_request_id")
    return has_column, key is not None


def migrate_ch05(engine: Engine) -> None:
    if engine.dialect.name not in {"sqlite", "mysql"}:
        raise RuntimeError(f"Unsupported migration dialect: {engine.dialect.name}")
    has_column, has_key = _inspect(engine)
    with engine.begin() as conn:
        if not has_column:
            conn.exec_driver_sql("ALTER TABLE tickets ADD COLUMN request_id VARCHAR(64) NULL")
        if not has_key:
            conn.exec_driver_sql(
                "CREATE UNIQUE INDEX uk_tickets_request_id ON tickets (request_id)"
            )
    _inspect(engine)


if __name__ == "__main__":
    from mewhelp.db.engine import engine

    migrate_ch05(engine)
    print("Ch05 nullable request_id and unique key verified; additive migration complete.")
