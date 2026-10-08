import importlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from langfuse import Langfuse
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def ch09_module():
    def require(name):
        try:
            return importlib.import_module('mewhelp.ch09.' + name)
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.startswith('mewhelp.ch09'):
                pytest.fail('Ch09 required contract is not implemented: ' + name)
            raise
    return require


@pytest.fixture
def span_client():
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    public_key = 'pk-test-' + uuid4().hex
    client = Langfuse(public_key=public_key, secret_key='sk-test',
                      base_url='http://127.0.0.1:3039', tracer_provider=provider,
                      span_exporter=exporter)
    yield client, exporter, public_key
    client.shutdown()


@pytest.fixture
def ch09_db(tmp_path):
    import mewhelp.db.models
    import mewhelp.knowledge.refusals
    import mewhelp.knowledge.store  # noqa: F401
    from mewhelp.db.base import Base
    engine = create_engine('sqlite:///' + (tmp_path / 'ch09.sqlite').as_posix())
    Base.metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def ch09_mysql():
    """Create and remove only a uniquely owned database with the mandated prefix."""
    from sqlalchemy.engine import make_url

    from mewhelp.ch09.migration import migrate_ch09
    from mewhelp.db.engine import MySqlSettings, build_url
    database = 'mewhelp_ch09_test_' + uuid4().hex[:16]
    assert database.startswith('mewhelp_ch09_test_')
    settings = MySqlSettings()
    admin = create_engine(make_url(build_url(settings)).set(database=None), pool_pre_ping=True)
    created = False
    engine = None
    try:
        with admin.begin() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE `{database}` CHARACTER SET utf8mb4')
            created = True
        engine = create_engine(make_url(build_url(settings)).set(database=database),
                               pool_pre_ping=True, json_serializer=lambda v: json.dumps(v, ensure_ascii=False))
        sql_root = Path(__file__).resolve().parents[2] / 'sql'
        with engine.begin() as connection:
            connection.exec_driver_sql('SET NAMES utf8mb4')
            for table, filename in (('conversations', 'ch02-ddl.sql'),
                                    ('messages', 'ch02-ddl.sql'),
                                    ('low_confidence_questions', 'ch04-ddl.sql')):
                ddl = (sql_root / filename).read_text(encoding='utf-8')
                start = ddl.index(f'CREATE TABLE {table} (')
                connection.exec_driver_sql(ddl[start:ddl.index(';', start)])
            connection.exec_driver_sql('ALTER TABLE messages ADD COLUMN citations JSON NULL, '
                'ADD COLUMN ch06_event_key VARCHAR(64) NULL, '
                'ADD UNIQUE KEY uk_messages_ch06_event_key (ch06_event_key)')
        migrate_ch09(engine)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            # Name was generated here, checked before creation and again before cleanup.
            assert database.startswith('mewhelp_ch09_test_') and database.isidentifier()
            with admin.begin() as connection:
                connection.exec_driver_sql(f'DROP DATABASE `{database}`')
        admin.dispose()
@pytest.fixture
def generation_context(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from mewhelp.ch05.limits import AgentLimits
    from mewhelp.ch05.state import WorkflowContext
    from mewhelp.db.base import Base
    from mewhelp.db.models import Conversation

    engine = create_engine('sqlite:///' + str(tmp_path / 'generation.sqlite3'))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        conversation = Conversation(session_id='s', user_id='u')
        db.add(conversation)
        db.commit()
        cid = conversation.id
    yield WorkflowContext(factory, None, None, AgentLimits()), cid
    engine.dispose()

