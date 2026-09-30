"""Apply additive Ch04 DDL after taking a database backup. Safe to rerun."""

from pathlib import Path

from sqlalchemy import JSON, Engine, Integer, String, inspect

from mewhelp.knowledge.refusals import LowConfidenceQuestion

DDL_PATH = Path(__file__).resolve().parents[1] / "sql/ch04-ddl.sql"


def _validate_existing(engine: Engine) -> tuple[set[str], bool, bool]:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    missing = {"conversations", "messages", "knowledge_chunks"} - tables
    if missing:
        raise RuntimeError(f"Create Ch02/Ch03 schema first: missing {sorted(missing)}")
    chunks = {col["name"]: col for col in inspector.get_columns("knowledge_chunks")}
    messages = {col["name"]: col for col in inspector.get_columns("messages")}
    if "product_category" in chunks:
        column = chunks["product_category"]
        if not (
            isinstance(column["type"], String)
            and column["type"].length == 128
            and column["nullable"]
        ):
            raise RuntimeError("Incompatible knowledge_chunks.product_category")
    if "citations" in messages:
        column = messages["citations"]
        if not (isinstance(column["type"], JSON) and column["nullable"]):
            raise RuntimeError("Incompatible messages.citations")
    if "low_confidence_questions" in tables:
        columns = {col["name"]: col for col in inspector.get_columns("low_confidence_questions")}
        expected = LowConfidenceQuestion.__table__
        if set(columns) != set(expected.columns.keys()):
            raise RuntimeError("Incompatible low_confidence_questions columns")
        if inspector.get_pk_constraint("low_confidence_questions")["constrained_columns"] != ["id"]:
            raise RuntimeError("Incompatible low_confidence_questions primary key")
        if engine.dialect.name == "mysql" and columns["id"].get("autoincrement") is not True:
            raise RuntimeError("Incompatible low_confidence_questions.id AUTO_INCREMENT")
        if engine.dialect.name == "sqlite" and not isinstance(columns["id"]["type"], Integer):
            raise RuntimeError("Incompatible low_confidence_questions.id integer primary key")
        for col in expected.columns:
            actual = columns[col.name]
            want_type = col.type.compile(dialect=engine.dialect).upper()
            actual_type = actual["type"].compile(dialect=engine.dialect).upper()
            if actual_type != want_type or actual["nullable"] != col.nullable:
                raise RuntimeError(f"Incompatible low_confidence_questions.{col.name}")
        fks = inspector.get_foreign_keys("low_confidence_questions")
        if not any(
            fk["constrained_columns"] == ["source_conversation_id"]
            and fk["referred_table"] == "conversations"
            and fk["referred_columns"] == ["id"]
            for fk in fks
        ):
            raise RuntimeError("Incompatible low_confidence_questions foreign key")
        indexes = {
            item["name"]: item["column_names"]
            for item in inspector.get_indexes("low_confidence_questions")
        }
        for index in expected.indexes:
            if indexes.get(index.name) != [column.name for column in index.columns]:
                raise RuntimeError(f"Incompatible low_confidence_questions index {index.name}")
    return tables, "product_category" in chunks, "citations" in messages


def migrate_ch04(engine: Engine) -> None:
    tables, has_category, has_citations = _validate_existing(engine)
    if engine.dialect.name == "mysql":
        sql = "\n".join(
            line
            for line in DDL_PATH.read_text(encoding="utf-8").splitlines()
            if not line.lstrip().startswith("--")
        )
        with engine.begin() as conn:
            for statement in sql.split(";"):
                statement = statement.strip()
                if not statement:
                    continue
                if statement.startswith("ALTER TABLE knowledge_chunks") and has_category:
                    continue
                if statement.startswith("ALTER TABLE messages") and has_citations:
                    continue
                if (
                    statement.startswith("CREATE TABLE low_confidence_questions")
                    and "low_confidence_questions" in tables
                ):
                    continue
                conn.exec_driver_sql(statement)
    elif engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            if not has_category:
                conn.exec_driver_sql(
                    "ALTER TABLE knowledge_chunks ADD COLUMN product_category VARCHAR(128) NULL"
                )
            if not has_citations:
                conn.exec_driver_sql("ALTER TABLE messages ADD COLUMN citations JSON NULL")
            if "low_confidence_questions" not in tables:
                LowConfidenceQuestion.__table__.create(conn)
    else:
        raise RuntimeError(f"Unsupported migration dialect: {engine.dialect.name}")
    _validate_existing(engine)


if __name__ == "__main__":
    from mewhelp.db.engine import engine

    migrate_ch04(engine)
    print("Ch04 schema verified; additive migration complete.")
