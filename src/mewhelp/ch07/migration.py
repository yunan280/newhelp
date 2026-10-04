import re
from pathlib import Path

from sqlalchemy import Engine, inspect

from mewhelp.db.models import Conversation, ConversationSummary


def _type_name(column_type, dialect):
    return re.sub(r'(BIGINT|INTEGER|INT)\(\d+\)', r'\1',
                  column_type.compile(dialect=dialect).upper())


def _validate(engine: Engine):
    inspector = inspect(engine)
    if not inspector.has_table('conversations'):
        raise RuntimeError('Create Ch02 schema before Ch07')
    actual = {c['name']: c for c in inspector.get_columns('conversations')}
    missing = []
    for name in ('summary', 'summary_upto_msg_id', 'layer1_from_msg_id'):
        wanted = Conversation.__table__.c[name]
        got = actual.get(name)
        if got is None:
            missing.append(name)
        elif (_type_name(got['type'], engine.dialect) != _type_name(wanted.type, engine.dialect)
              or not got['nullable']):
            raise RuntimeError(f'Incompatible conversations.{name}')
    exists = inspector.has_table('conversation_summaries')
    if exists:
        table = ConversationSummary.__table__
        columns = {c['name']: c for c in inspector.get_columns(table.name)}
        if set(columns) != set(table.columns.keys()):
            raise RuntimeError('Incompatible conversation_summaries columns')
        for col in table.columns:
            got = columns[col.name]
            if (_type_name(got['type'], engine.dialect) != _type_name(col.type, engine.dialect)
                or got['nullable'] != col.nullable):
                raise RuntimeError(f'Incompatible conversation_summaries.{col.name}')
            if col.server_default is not None and str(got.get('default') or '').strip("'\"() ").upper() != 'CURRENT_TIMESTAMP':
                raise RuntimeError(f'Incompatible conversation_summaries.{col.name} default')
        if inspector.get_pk_constraint(table.name)['constrained_columns'] != ['id']:
            raise RuntimeError('Incompatible conversation_summaries primary key')
        if engine.dialect.name == 'mysql' and not columns['id'].get('autoincrement'):
            raise RuntimeError('Incompatible conversation_summaries AUTO_INCREMENT')
        indexes = {i['name']: i for i in inspector.get_indexes(table.name)}
        for index in table.indexes:
            got = indexes.get(index.name)
            if (not got or got['column_names'] != [c.name for c in index.columns]
                or bool(got['unique']) != index.unique):
                raise RuntimeError(f'Incompatible summary index {index.name}')
        if inspector.get_foreign_keys(table.name):
            raise RuntimeError('Incompatible summary foreign keys; user DDL has none')
    return missing, exists


def migrate_ch07(engine: Engine) -> dict:
    if engine.dialect.name not in {'sqlite', 'mysql'}:
        raise RuntimeError('Ch07 supports MySQL and SQLite test schemas')
    missing, exists = _validate(engine)
    table = Conversation.__table__
    # MySQL DDL commits implicitly. Validate before each additive, restartable stage.
    with engine.begin() as conn:
        if engine.dialect.name == 'mysql':
            conn.exec_driver_sql('SET NAMES utf8mb4')
        for name in missing:
            col = table.c[name]
            clause = f'ALTER TABLE conversations ADD COLUMN {name} {col.type.compile(dialect=engine.dialect)} NULL'
            if engine.dialect.name == 'mysql':
                after = {'summary': 'status', 'summary_upto_msg_id': 'summary',
                         'layer1_from_msg_id': 'summary_upto_msg_id'}[name]
                clause += f" COMMENT '{col.comment}' AFTER {after}"
            conn.exec_driver_sql(clause)
        if not exists:
            ConversationSummary.__table__.create(conn)
    _validate(engine)
    return {'added': [*missing, *([] if exists else ['conversation_summaries'])],
            'ddl': [str(Path('sql/ch07-ddl.sql')), str(Path('sql/ch07-layers.sql'))]}
