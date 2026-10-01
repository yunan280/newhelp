import json

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mewhelp.ch05.config import Ch05Settings
from mewhelp.ch05.schemas import TurnRequest
from mewhelp.db.base import Base
from mewhelp.knowledge.answering import SourceDTO


class ModelFactory:
    def __init__(self):
        self.intents = []
        self.decisions = []
        self.requests = []
        self.fail_classifier = False
        self.fail_answer = False
        self.block_answer = None

    def __call__(self, output_tokens, **kwargs):
        return FakeModel(
            self,
            "answer"
            if kwargs.get("streaming")
            else "classifier"
            if output_tokens == 128
            else "decision",
        )


class FakeModel:
    def __init__(self, owner, kind):
        self.owner, self.kind = owner, kind

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.owner.requests.append((self.kind, list(messages)))
        if self.kind == "classifier":
            if self.owner.fail_classifier:
                raise RuntimeError("classifier unavailable")
            return AIMessage(
                content=json.dumps(
                    {"intent": self.owner.intents.pop(0) if self.owner.intents else "退款退货"},
                    ensure_ascii=False,
                )
            )
        if self.owner.decisions:
            return self.owner.decisions.pop(0)
        return AIMessage(
            content='{"reply_mode":"answer","suggested_actions":[],"ticket_type":null}'
        )

    async def astream(self, messages):
        self.owner.requests.append((self.kind, list(messages)))
        if self.owner.fail_answer:
            raise RuntimeError("answer unavailable")
        for part in ["根据结果", "为您回答[1]。"]:
            yield AIMessageChunk(content=part)
            if self.owner.block_answer:
                await self.owner.block_answer.wait()


@pytest.fixture
def model_factory():
    return ModelFactory()


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'business.sqlite3'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def checkpoint_settings(tmp_path):
    return Ch05Settings(checkpoint_path=tmp_path / "checkpoints.sqlite3")


@pytest.fixture
def strong_evidence():
    try:
        from mewhelp.ch05.evidence import EvidenceEnvelope
    except ImportError:
        pytest.fail("evidence module missing")
    source = SourceDTO(
        number=1,
        chunk_id="1",
        questions="退货",
        answer="七天无理由",
        section_path="退货政策",
        category="政策",
        product_category=None,
        content_hash="a" * 64,
        source_url="/kb/source/1",
    )
    return EvidenceEnvelope(sources=[source], scores=[0.9], threshold=0.5, context_budget=32000)


@pytest.fixture
async def workflow_runtime(
    session_factory, checkpoint_settings, model_factory, strong_evidence, monkeypatch
):
    try:
        from mewhelp.ch05 import workflow
        from mewhelp.ch05.runtime import open_runtime
    except ImportError:
        pytest.fail("persistent graph runtime missing")

    async def retrieve(question, *, rag, filters):
        return strong_evidence

    monkeypatch.setattr(workflow, "retrieve_knowledge", retrieve)
    async with open_runtime(
        session_factory,
        settings=checkpoint_settings,
        model_factory=model_factory,
        rag_factory=lambda: object(),
    ) as runtime:
        yield runtime


@pytest.fixture
def knowledge_request():
    return TurnRequest(message="七天无理由退货政策是什么", session_id="knowledge-test")
