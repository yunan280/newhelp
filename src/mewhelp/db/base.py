"""声明式基类。

`type_annotation_map` 不是装饰:`Mapped[str]` 默认映射到**无长度**的 `String`,
而 MySQL 的 `VARCHAR` 不接受无长度声明。没有这张表,第一次 create_all 就会在
MySQL 上炸;SQLite 不校验长度,所以这个坑在测试里**看不见**,只在真机上炸。
"""

import datetime as dt

from sqlalchemy import DateTime, String, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    type_annotation_map = {
        # 默认给 255。需要更长的地方显式 mapped_column(String(N)) 覆盖。
        str: String(255),
        dt.datetime: DateTime,
    }


# SQLite 默认**不**强制外键 —— 不打开 PRAGMA 的话,conversation_id=999 的孤儿
# 消息会被照单全收,而 MySQL 会拒。测试要能代表真机行为,所以补上。
#
# 挂在 `Engine` 这个全局类上而不是某个 engine 实例上:测试每条用例都新建
# `create_engine("sqlite://")`,挂在实例上就得在 fixture 里重复一遍,漏一次
# 那条外键用例就变成假绿。非 SQLite 的连接靠模块名挡掉(MySQL 侧
# dbapi_connection 是 `pymysql.connections.Connection`)。
@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    if type(dbapi_connection).__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
