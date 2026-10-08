"""Run the additive migration against an old schema and reject drift."""

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable


def migration():
    path = Path(__file__).parents[1] / "scripts/migrate_ch04_schema.py"
    assert path.is_file(), "Ch04 incremental migration is missing"
    spec = importlib.util.spec_from_file_location("ch04_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def old_engine():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE conversations (id INTEGER PRIMARY KEY)")
        conn.exec_driver_sql("CREATE TABLE messages (id INTEGER PRIMARY KEY, content TEXT)")
        conn.exec_driver_sql("CREATE TABLE knowledge_chunks (id INTEGER PRIMARY KEY, category VARCHAR(255))")
    return engine


def test_migration_adds_columns_and_is_idempotent():
    engine = old_engine()
    migration().migrate_ch04(engine)
    migration().migrate_ch04(engine)
    inspector = inspect(engine)
    chunks = {column["name"]: column for column in inspector.get_columns("knowledge_chunks")}
    assert chunks["product_category"]["type"].length == 128
    assert chunks["product_category"]["nullable"] is True
    messages = {column["name"] for column in inspector.get_columns("messages")}
    assert "citations" in messages
    assert "low_confidence_questions" in inspector.get_table_names()


def test_migration_rejects_incompatible_existing_column_before_changes():
    engine = old_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("ALTER TABLE knowledge_chunks ADD product_category INTEGER")
    with pytest.raises(RuntimeError, match="product_category"):
        migration().migrate_ch04(engine)
    assert "citations" not in {col["name"] for col in inspect(engine).get_columns("messages")}


def test_migration_rejects_incomplete_existing_pool():
    engine = old_engine()
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE low_confidence_questions (id INTEGER PRIMARY KEY)")
    with pytest.raises(RuntimeError, match="low_confidence_questions"):
        migration().migrate_ch04(engine)


def test_migration_rejects_pool_with_missing_index():
    engine = old_engine()
    migration().migrate_ch04(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP INDEX idx_low_confidence_created_at")
    with pytest.raises(RuntimeError, match="index"):
        migration().migrate_ch04(engine)


def test_ch04_mysql_schema_matches_delta_and_foreign_key():
    from mewhelp.db.models import Message
    from mewhelp.knowledge.refusals import LowConfidenceQuestion
    from mewhelp.knowledge.store import KnowledgeChunk

    ddl = (Path(__file__).parents[1] / "sql/ch04-ddl.sql").read_text(encoding="utf-8")
    compiled = str(CreateTable(LowConfidenceQuestion.__table__).compile(dialect=mysql.dialect()))
    assert set(LowConfidenceQuestion.__table__.columns.keys()) == {
        "id", "original_question", "source_conversation_id", "entry_point", "trigger_stage",
        "reason_code", "reason", "created_at",
        "retrieved_chunks", "matched_review_id",
    }
    assert "BIGINT UNSIGNED" in compiled
    assert "fk_low_confidence_conversation" in compiled and "fk_low_confidence_conversation" in ddl
    assert KnowledgeChunk.__table__.c.product_category.type.length == 128
    assert Message.__table__.c.citations.nullable
    assert "ALTER TABLE knowledge_chunks" in ddl and "ALTER TABLE messages" in ddl
    ch09_ddl = (Path(__file__).parents[1] / 'sql/ch09.sql').read_text(encoding='utf-8')
    assert 'ADD COLUMN retrieved_chunks' in ch09_ddl
    assert 'ADD COLUMN matched_review_id' in ch09_ddl
    assert 'fk_lcq_review' in compiled and 'fk_lcq_review' in ch09_ddl
    assert LowConfidenceQuestion.__table__.c.entry_point.type.enums == ['chat_stream', 'agent', 'cli', 'feedback']
    assert LowConfidenceQuestion.__table__.c.trigger_stage.type.enums == ['retrieval', 'generation', 'feedback']
