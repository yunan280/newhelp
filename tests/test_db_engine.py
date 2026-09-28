"""MySQL 连接串的拼装 —— 不连库,纯字符串。

真正的连库验证在真机冒烟里(需要 Docker 起来),不在默认套件里。
"""

import pytest
from sqlalchemy.engine import make_url

from mewhelp.db.engine import MySqlSettings, build_url


def settings(**overrides) -> MySqlSettings:
    base = {
        "mysql_host": "127.0.0.1",
        "mysql_port": 3306,
        "mysql_user": "root",
        "mysql_password": "pw",
        "mysql_database": "mewhelp",
    }
    base.update(overrides)
    # 用构造参数而不是读 .env —— 测试不该依赖本机 .env 的内容
    return MySqlSettings(_env_file=None, **base)


def test_url_is_pymysql_and_carries_utf8mb4():
    """charset=utf8mb4 是承重的:漏了它,中文写进去就是乱码。

    与 sql/ch02-ddl.sql 里的 SET NAMES utf8mb4 是同一件事的两端 ——
    一端管 CLI 执行 .sql,一端管 Python 写库。
    """
    url = make_url(build_url(settings()))
    assert url.drivername == "mysql+pymysql"
    assert url.query.get("charset") == "utf8mb4"


def test_url_carries_host_port_and_database():
    url = make_url(build_url(settings(mysql_host="db", mysql_port=3307, mysql_database="mew")))
    assert (url.host, url.port, url.database) == ("db", 3307, "mew")


@pytest.mark.parametrize(
    "password",
    [
        pytest.param("p@ss:w/rd", id="含@与:与/"),
        pytest.param("中文密码", id="含中文"),
        pytest.param("a#b?c", id="含#与?"),
    ],
)
def test_special_characters_in_the_password_do_not_break_the_url(password):
    """密码里有 @ / : / ? / # 时不转义会把 URL 解析歪 —— 端口跑到密码里、
    或数据库名变成 `rd`。这类密码极常见,而失败症状是"连不上",不是"密码错"。
    """
    url = make_url(build_url(settings(mysql_password=password)))
    assert url.password == password
    assert url.database == "mewhelp"
    assert url.port == 3306


def test_engine_is_lazy_and_does_not_connect_at_import():
    """导入 db.engine 不能触发连库 —— 否则没起 Docker 时连测试都收集不了。"""
    import mewhelp.db.engine as mod

    assert mod.engine is not None
    assert mod.engine.url.drivername == "mysql+pymysql"
    # 没有真的连过:pool 里还没有任何连接
    assert mod.engine.pool.checkedout() == 0


def test_session_factory_produces_sessions_without_connecting():
    """`SessionFactory` 是下游客工具与编排层唯一依赖的形状:可调用、返回 Session。

    这条替代了计划里那句 `assert issubclass(Session, Session)` —— 那是个恒真式,
    什么也没钉住。真正要钉的是**两件事**:造得出来 Session,而且造的过程不连库。
    后者对本章特别重要:工具跑在线程池里,每个工具调用都要自己开一个 Session
    (spec §9.2),所以"开 Session"必须是廉价的、不需要活性连接的。
    """
    from sqlalchemy.orm import Session

    import mewhelp.db.engine as mod

    session = mod.SessionFactory()
    try:
        assert isinstance(session, Session)
        assert mod.engine.pool.checkedout() == 0
    finally:
        session.close()


def test_get_session_is_a_generator_dependency():
    """FastAPI 依赖形状:生成器,yield 一个 Session,退出时关闭。"""
    import inspect

    from mewhelp.db.engine import get_session

    assert inspect.isgeneratorfunction(get_session)
