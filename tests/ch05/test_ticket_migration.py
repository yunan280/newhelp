import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError


def migrate(engine):
    try:
        from scripts.migrate_ch05_schema import migrate_ch05
    except ImportError:
        pytest.fail("additive ticket migration missing")
    return migrate_ch05(engine)


def test_upgrade_twice_preserves_rows_and_enforces_unique_key():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE tickets (ticket_no VARCHAR(32) PRIMARY KEY)")
        conn.exec_driver_sql("INSERT INTO tickets VALUES ('old-ticket')")
    migrate(engine)
    migrate(engine)
    with engine.begin() as conn:
        assert conn.exec_driver_sql("SELECT ticket_no FROM tickets").scalar_one() == "old-ticket"
        conn.exec_driver_sql("INSERT INTO tickets VALUES ('one', 'request-one')")
        with pytest.raises(IntegrityError):
            conn.exec_driver_sql("INSERT INTO tickets VALUES ('two', 'request-one')")
    col = next(c for c in inspect(engine).get_columns("tickets") if c["name"] == "request_id")
    assert col["nullable"] and col["type"].length == 64
    engine.dispose()


@pytest.mark.parametrize(
    "column,index",
    [
        ("request_id VARCHAR(32) NULL", ""),
        ("request_id VARCHAR(64) NOT NULL", ""),
        (
            "request_id VARCHAR(64) NULL",
            "CREATE INDEX uk_tickets_request_id ON tickets(request_id)",
        ),
    ],
)
def test_incompatible_existing_schema_is_rejected(column, index):
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql(f"CREATE TABLE tickets (ticket_no VARCHAR(32) PRIMARY KEY, {column})")
        if index:
            conn.exec_driver_sql(index)
    with pytest.raises(RuntimeError, match="Incompatible"):
        migrate(engine)
    engine.dispose()
