import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import mysql


def test_migration_rejects_sqlite_instead_of_claiming_mysql_acceptance(ch09_module):
    engine = create_engine('sqlite://')
    try:
        with pytest.raises(ValueError, match='MySQL'):
            ch09_module('migration').migrate_ch09(engine)
    finally:
        engine.dispose()


@pytest.mark.parametrize('actual,expected', [
    (mysql.VARCHAR(100), mysql.VARCHAR(512)),
    (mysql.ENUM('待审', '通过'), mysql.ENUM('待审', '通过', '驳回')),
    (mysql.BIGINT(unsigned=False), mysql.BIGINT(unsigned=True)),
    (mysql.TEXT(), mysql.JSON()),
])
def test_incompatible_column_is_rejected(ch09_module, actual, expected):
    check = ch09_module('migration').validate_column
    with pytest.raises(ValueError, match='incompatible'):
        check('review_queue', 'test', {'type': actual, 'nullable': False},
              {'type': expected, 'nullable': False})
