"""Apply the authoritative MySQL DDL incrementally, refusing incompatible drift."""
import re
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.dialects import mysql

from mewhelp.db.models import EvalRun, Message, ReviewQueue
from mewhelp.knowledge.refusals import LowConfidenceQuestion

DDL_PATH = Path(__file__).resolve().parents[3] / 'sql/ch09.sql'


def _type_signature(value):
    if getattr(value, 'enums', None) is not None:
        return ('ENUM', tuple(value.enums))
    compiled = value.compile(dialect=mysql.dialect()).upper()
    compiled = re.sub(r'\b(BIGINT|INTEGER|INT)\(\d+\)', r'\1', compiled)
    return re.sub(r'\s+(?:CHARACTER SET|COLLATE)\s+\S+', '', compiled).replace('INTEGER', 'INT')


def validate_column(table, name, actual, expected):
    if (_type_signature(actual['type']) != _type_signature(expected['type'])
        or actual['nullable'] != expected['nullable']):
        raise ValueError(f'incompatible column {table}.{name}')
    if 'default' in expected:
        def normalize(value):
            return (str(value).strip("'").lower().replace('()', '')
                    if value is not None else None)
        if normalize(actual.get('default')) != normalize(expected['default']):
            raise ValueError(f'incompatible default {table}.{name}')


def _expected(column, *, defaults=False):
    value = {'type': column.type, 'nullable': column.nullable}
    if defaults:
        value['default'] = str(column.server_default.arg) if column.server_default else None
    return value


def _create_statement(name):
    ddl = DDL_PATH.read_text(encoding='utf-8')
    start = ddl.index(f'CREATE TABLE {name} (')
    return ddl[start:ddl.index(';', start)]


def _assert_table(connection, model):
    inspector = inspect(connection)
    table = model.__table__
    columns = {c['name']: c for c in inspector.get_columns(table.name)}
    for col in table.columns:
        if col.name not in columns:
            raise ValueError(f'incompatible missing column {table.name}.{col.name}')
        expected = _expected(col, defaults=col.name != 'id')
        if table.name == 'review_queue' and col.name == 'updated_at':
            expected.pop('default', None)
        validate_column(table.name, col.name, columns[col.name], expected)
    pk = inspector.get_pk_constraint(table.name)
    if pk['constrained_columns'] != ['id'] or not columns['id'].get('autoincrement'):
        raise ValueError(f'incompatible primary key {table.name}')
    indexes = {i['name']: i for i in inspector.get_indexes(table.name)}
    for index in table.indexes:
        actual = indexes.get(index.name)
        if actual is None or actual['column_names'] != [c.name for c in index.columns] or actual['unique']:
            raise ValueError(f'incompatible index {table.name}.{index.name}')
    ddl = connection.exec_driver_sql(f'SHOW CREATE TABLE `{table.name}`').one()[1]
    if 'ENGINE=InnoDB' not in ddl or 'CHARSET=utf8mb4' not in ddl:
        raise ValueError(f'incompatible engine/charset {table.name}')
    if table.name == 'review_queue' and 'ON UPDATE CURRENT_TIMESTAMP' not in ddl.upper():
        raise ValueError('incompatible review_queue.updated_at ON UPDATE')


def _columns(connection, table):
    return {c['name']: c for c in inspect(connection).get_columns(table)}


def migrate_ch09(engine):
    if engine.dialect.name != 'mysql':
        raise ValueError('Ch09 authority migration requires real MySQL')
    changed = []
    with engine.begin() as connection:
        connection.exec_driver_sql('SET NAMES utf8mb4')
        connection.exec_driver_sql("SET time_zone = '+00:00'")
        tables = inspect(connection).get_table_names()
        if not {'messages', 'low_confidence_questions'} <= set(tables):
            raise ValueError('Ch02/Ch04 schema must exist before Ch09 migration')
        # Check existing objects before applying any changes. MySQL DDL commits implicitly.
        for model in (ReviewQueue, EvalRun):
            if model.__tablename__ in tables:
                _assert_table(connection, model)
        additions = [('low_confidence_questions', 'retrieved_chunks',
            "JSON NULL COMMENT '落池时的召回片段快照:Top 几条的原文与得分,审核页展示用;没走检索为 NULL' AFTER reason"),
            ('low_confidence_questions', 'matched_review_id',
             "BIGINT UNSIGNED NULL COMMENT '查重后归并到的缺口,指向 review_queue.id' AFTER retrieved_chunks"),
            ('messages', 'retrieval_snapshot', "JSON NULL COMMENT 'Ch09同轮检索快照、原话身份与反馈标记'")]
        models = {'low_confidence_questions': LowConfidenceQuestion, 'messages': Message}
        for table, name, _ in additions:
            actual = _columns(connection, table).get(name)
            if actual:
                validate_column(table, name, actual, _expected(models[table].__table__.c[name]))
        old_enums = {'entry_point': ('chat_stream', 'agent', 'cli'),
                     'trigger_stage': ('retrieval', 'generation')}
        pool = _columns(connection, 'low_confidence_questions')
        for name, old in old_enums.items():
            actual = pool[name]
            allowed = [old, (*old, 'feedback')]
            if tuple(getattr(actual['type'], 'enums', ())) not in allowed or actual['nullable']:
                raise ValueError(f'incompatible column low_confidence_questions.{name}')
        for model in (ReviewQueue, EvalRun):
            if model.__tablename__ not in tables:
                connection.exec_driver_sql(_create_statement(model.__tablename__))
                changed.append('create:' + model.__tablename__)
        for table, name, definition in additions:
            if name not in _columns(connection, table):
                connection.exec_driver_sql(f'ALTER TABLE `{table}` ADD COLUMN `{name}` {definition}')
                changed.append(f'add:{table}.{name}')
        for name, old in old_enums.items():
            if tuple(_columns(connection, 'low_confidence_questions')[name]['type'].enums) == old:
                values = ','.join("'" + v + "'" for v in (*old, 'feedback'))
                connection.exec_driver_sql('ALTER TABLE low_confidence_questions '
                                          f'MODIFY COLUMN `{name}` ENUM({values}) NOT NULL')
                changed.append('extend:' + name)
        indexes = {i['name']: i for i in inspect(connection).get_indexes('low_confidence_questions')}
        index = indexes.get('idx_matched_review_id')
        if index is not None and (index['column_names'] != ['matched_review_id'] or index['unique']):
            raise ValueError('incompatible index low_confidence_questions.idx_matched_review_id')
        if index is None:
            connection.exec_driver_sql('ALTER TABLE low_confidence_questions '
                                      'ADD KEY idx_matched_review_id (matched_review_id)')
            changed.append('index:idx_matched_review_id')
        foreign = inspect(connection).get_foreign_keys('low_confidence_questions')
        existing = [fk for fk in foreign if fk['name'] == 'fk_lcq_review' or
                    'matched_review_id' in fk['constrained_columns']]
        if existing:
            fk = existing[0]
            if (len(existing) != 1 or fk['name'] != 'fk_lcq_review'
                or fk['constrained_columns'] != ['matched_review_id']
                or fk['referred_table'] != 'review_queue' or fk['referred_columns'] != ['id']
                or fk['options'].get('ondelete', '').upper() != 'SET NULL'):
                raise ValueError('incompatible foreign key fk_lcq_review')
        else:
            connection.exec_driver_sql('ALTER TABLE low_confidence_questions ADD CONSTRAINT fk_lcq_review '
                'FOREIGN KEY (matched_review_id) REFERENCES review_queue (id) ON DELETE SET NULL')
            changed.append('foreign_key:fk_lcq_review')
        for model in (ReviewQueue, EvalRun):
            _assert_table(connection, model)
        schema = {table: connection.exec_driver_sql(f'SHOW CREATE TABLE `{table}`').one()[1]
                  for table in ('review_queue', 'eval_runs', 'low_confidence_questions', 'messages')}
    return {'changed': changed, 'schema': schema, 'charset': 'utf8mb4', 'timezone': 'UTC'}
