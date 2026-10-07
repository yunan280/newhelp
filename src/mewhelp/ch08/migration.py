"""仅新增审计表；已有表不兼容时停止，不破坏数据。"""
import re

from sqlalchemy import Engine, inspect

from mewhelp.db.models import ToolAuditLog


def migrate_ch08(engine: Engine) -> dict:
    if engine.dialect.name not in {'mysql', 'sqlite'}:
        raise RuntimeError('Unsupported audit migration dialect')
    table = ToolAuditLog.__table__
    inspector = inspect(engine)
    created = table.name not in inspector.get_table_names()
    if created:
        with engine.begin() as conn:
            if engine.dialect.name == 'mysql':
                conn.exec_driver_sql('SET NAMES utf8mb4')
            table.create(conn)
        inspector = inspect(engine)
    actual = {c['name']: c for c in inspector.get_columns(table.name)}
    if set(actual) != set(table.columns.keys()) or inspector.get_foreign_keys(table.name):
        raise RuntimeError('Incompatible tool_audit_logs columns/foreign keys')
    if inspector.get_pk_constraint(table.name)['constrained_columns'] != ['id']:
        raise RuntimeError('Incompatible audit primary key')
    for col in table.columns:
        got = actual[col.name]
        def canonical(value):
            return re.sub(r'(BIGINT|INTEGER|TINYINT)\(\d+\)', r'\1', value.upper())
        if canonical(got['type'].compile(dialect=engine.dialect)) != canonical(col.type.compile(dialect=engine.dialect)) or got['nullable'] != col.nullable:
            raise RuntimeError(f'Incompatible audit column {col.name}')
        wanted = str(col.server_default.arg) if col.server_default else ''
        def default(value):
            return str(value or '').strip("'\"() ").replace('()', '').upper()
        if default(got.get('default')) != default(wanted):
            raise RuntimeError(f'Incompatible audit default {col.name}')
        if engine.dialect.name == 'mysql' and got.get('comment') != col.comment:
            raise RuntimeError(f'Incompatible audit comment {col.name}')
    indexes = {i['name']: i for i in inspector.get_indexes(table.name)}
    expected = {i.name: [c.name for c in i.columns] for i in table.indexes
                if engine.dialect.name == 'mysql' or i.name not in {'idx_conversation_id', 'idx_status'}}
    if set(indexes) != set(expected) or any(indexes[n]['column_names'] != cols or indexes[n]['unique'] for n, cols in expected.items()):
        raise RuntimeError('Incompatible audit indexes')
    if engine.dialect.name == 'mysql':
        options = inspector.get_table_options(table.name)
        if options.get('mysql_engine') != 'InnoDB' or not str(options.get('mysql_default charset', options.get('mysql_charset', ''))).startswith('utf8mb4'):
            raise RuntimeError('Incompatible audit engine/charset')
        if actual['id'].get('autoincrement') is not True:
            raise RuntimeError('Incompatible audit AUTO_INCREMENT')
    return {'created': created, 'table': table.name}
