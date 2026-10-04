from importlib import import_module
from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateTable

from mewhelp.db.models import Conversation


def migration():
    try:
        return import_module('mewhelp.ch07.migration')
    except ModuleNotFoundError:
        pytest.fail('Ch07 must support validated restartable two-stage migration')


def test_user_ddl_and_orm_match():
    migration()
    from mewhelp.db.models import ConversationSummary
    ddl = str(CreateTable(ConversationSummary.__table__).compile(dialect=mysql.dialect()))
    assert 'BIGINT UNSIGNED' in ddl and 'FOREIGN KEY' not in ddl
    assert set(ConversationSummary.__table__.columns.keys()) == {
        'id', 'conversation_id', 'seq', 'from_msg_id', 'upto_msg_id', 'content', 'created_at'}
    assert {i.name: ([c.name for c in i.columns], i.unique)
            for i in ConversationSummary.__table__.indexes} == {
        'uk_conv_seq': (['conversation_id', 'seq'], True),
        'idx_conv_upto': (['conversation_id', 'upto_msg_id'], False)}
    for name in ('summary', 'summary_upto_msg_id', 'layer1_from_msg_id'):
        assert Conversation.__table__.c[name].nullable
    for file in ('ch07-ddl.sql', 'ch07-layers.sql'):
        assert 'SET NAMES utf8mb4;' in Path('sql', file).read_text(encoding='utf-8')


def test_half_applied_migration_is_restartable(tmp_path):
    module = migration()
    engine = create_engine(f'sqlite:///{tmp_path / "db.sqlite"}')
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE conversations (id INTEGER PRIMARY KEY, summary TEXT NULL, '
                             'summary_upto_msg_id INTEGER NULL)')
    module.migrate_ch07(engine)
    result = module.migrate_ch07(engine)
    assert result['added'] == []
    assert 'layer1_from_msg_id' in [c['name'] for c in inspect(engine).get_columns('conversations')]
    assert 'conversation_summaries' in inspect(engine).get_table_names()
    engine.dispose()


def test_wrong_existing_type_stops(tmp_path):
    module = migration()
    engine = create_engine(f'sqlite:///{tmp_path / "db.sqlite"}')
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE conversations (id INTEGER PRIMARY KEY, summary INTEGER)')
    with pytest.raises(RuntimeError, match='summary'):
        module.migrate_ch07(engine)
    assert 'conversation_summaries' not in inspect(engine).get_table_names()
    engine.dispose()
