"""Connection-scoped MySQL locks, never an open transaction across model I/O."""

import hashlib
from contextlib import contextmanager

from sqlalchemy import text


@contextmanager
def named_lock(engine, *, scope, key="", wait_seconds=0):
    if engine.dialect.name != "mysql":
        raise ValueError("named_lock requires real MySQL; tests must explicitly inject their lock")
    name = (
        "ch09:" + hashlib.sha256(f"{engine.url.database}:{scope}:{key}".encode()).hexdigest()[:59]
    )
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        acquired = (
            connection.scalar(
                text("SELECT GET_LOCK(:name,:wait)"), {"name": name, "wait": wait_seconds}
            )
            == 1
        )
        try:
            yield acquired
        finally:
            if acquired:
                try:
                    released = connection.scalar(text("SELECT RELEASE_LOCK(:name)"), {"name": name})
                    if released != 1:
                        connection.invalidate()
                except BaseException:
                    connection.invalidate()
                    raise
