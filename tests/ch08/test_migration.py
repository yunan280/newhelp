import pytest
from sqlalchemy import create_engine, inspect, text


def test_migration_checks_existing_table_without_rebuild(tmp_path):
    from mewhelp.ch08.migration import migrate_ch08
    engine = create_engine(f'sqlite:///{tmp_path / "migrate.db"}')
    assert migrate_ch08(engine)['created']
    with engine.begin() as db:
        db.execute(text("INSERT INTO tool_audit_logs(tool_name, tool_source, status) VALUES ('x','builtin','成功')"))
    assert not migrate_ch08(engine)['created']
    with engine.connect() as db:
        assert db.scalar(text('SELECT COUNT(*) FROM tool_audit_logs')) == 1
    assert not inspect(engine).get_foreign_keys('tool_audit_logs')


def test_incompatible_existing_table_is_not_rebuilt():
    from mewhelp.ch08.migration import migrate_ch08
    engine = create_engine('sqlite://')
    with engine.begin() as db:
        db.execute(text('CREATE TABLE tool_audit_logs(id INTEGER PRIMARY KEY)'))
    with pytest.raises(RuntimeError):
        migrate_ch08(engine)
    assert len(inspect(engine).get_columns('tool_audit_logs')) == 1
