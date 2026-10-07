import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mewhelp.db.base import Base


@pytest.fixture
def audit_factory(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "audit.db"}', connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()
