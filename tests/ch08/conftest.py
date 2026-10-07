import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mewhelp.db import models  # noqa: F401 — populate metadata before fixtures create tables
from mewhelp.db.base import Base
from tests.ch05.conftest import (  # noqa: F401 — real SQLite/graph, scripted external model only
    checkpoint_settings,
    model_factory,
    router_settings,
    session_factory,
    strong_evidence,
    workflow_runtime,
)


@pytest.fixture
def audit_factory(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "audit.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()
