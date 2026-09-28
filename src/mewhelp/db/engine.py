"""MySQL 接入。

MYSQL_* 的读取放在这里,不放 config.py —— config.py 是 ch01 的东西,本章一行不改。
`extra="ignore"` 让两边共用同一份 .env 而不互相要求。

同步引擎:异步(asyncmy)实测要 +3 个依赖(greenlet / asyncmy / aiosqlite 全缺),
换来的是本章不需要的并发。工具函数的阻塞由 langchain 的 @tool 代劳 ——
它对同步函数的 ainvoke **已经**把调用丢进线程池(实测跑在 asyncio_0 线程)。
"""

from collections.abc import Iterator
from functools import lru_cache
from urllib.parse import quote_plus

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


class MySqlSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "mewhelp"


@lru_cache
def get_mysql_settings() -> MySqlSettings:
    return MySqlSettings()


def build_url(s: MySqlSettings) -> str:
    """拼 pymysql 连接串。

    `quote_plus` 不是可选的:密码里出现 @ : / ? # 时,不转义会把 URL 解析歪 ——
    端口跑到密码里、数据库名被截断 —— 而症状是"连不上",不是"密码错",
    排查方向会被带偏。这类密码很常见。
    """
    return (
        f"mysql+pymysql://{quote_plus(s.mysql_user)}:{quote_plus(s.mysql_password)}"
        f"@{s.mysql_host}:{s.mysql_port}/{s.mysql_database}?charset=utf8mb4"
    )


# 引擎是惰性的:create_engine 不建连接。所以导入本模块不会连库 ——
# 没起 Docker 时测试照样能收集、能跑。
engine = create_engine(build_url(get_mysql_settings()), pool_pre_ping=True, future=True)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# 下游客工具与编排层只依赖这个形状 —— 收工厂而不是收 Session,
# 因为工具跑在线程池里,SQLAlchemy 的 Session 非线程安全(见 spec §9.2 的偏离说明)。
#
# 这里绑的是 **SessionLocal 这个实例**,不是 `type(SessionLocal)`。计划原本写的是后者,
# 那取到的是 `sessionmaker` **这个类** —— `SessionFactory()` 会造出**另一个没绑 engine 的
# sessionmaker**,而不是 Session,下游 `with session_factory() as session` 全部炸在
# UnboundExecutionError 上。下游一律是「调一次、拿一个 Session、当上下文管理器用」,
# 所以这里要的正是 sessionmaker 实例本身。
SessionFactory = SessionLocal


def get_session() -> Iterator[Session]:
    """FastAPI 依赖:每请求一个 Session。"""
    with SessionLocal() as session:
        yield session
