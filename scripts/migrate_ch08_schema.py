"""新增并检查 Ch08 审计表，不修改已有业务表。"""
from mewhelp.ch08.migration import migrate_ch08
from mewhelp.db.engine import engine

if __name__ == '__main__':
    print(migrate_ch08(engine))
