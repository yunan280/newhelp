"""Apply both validated Ch07 DDL stages; take a consistent database backup first."""
from mewhelp.ch07.migration import migrate_ch07

if __name__ == '__main__':
    from mewhelp.db.engine import engine
    print(migrate_ch07(engine))
