import json

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mewhelp.ch05.config import Ch05Settings
from mewhelp.ch05.intent import match_chitchat
from mewhelp.ch05.schemas import TurnRequest
from mewhelp.ch06.config import Ch06Settings
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
        self.understanding_scope = "general"
        self.understanding_outputs = []
        self.assessment_verdict = "eligible"

    def __call__(self, output_tokens, **kwargs):
        return FakeModel(
            self,
            "answer"
            if kwargs.get("streaming")
            else "classifier"
            if output_tokens == 128
            else "decision",
        )

    def router(self, *, purpose, **kwargs):
        return FakeModel(self, purpose)


class FakeModel:
    def __init__(self, owner, kind):
        self.owner, self.kind = owner, kind

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages):
        self.owner.requests.append((self.kind, list(messages)))
        if self.kind == "understanding":
            payload = json.loads(messages[-1].content)
            parsed = self.owner.understanding_outputs.pop(0) if self.owner.understanding_outputs else {
                "question": payload["question"], "scope": self.owner.understanding_scope,
                "reference_order_id": None, "reference_message_id": None,
            }
            return AIMessage(content=json.dumps(parsed, ensure_ascii=False))
        if self.kind == "expansion":
            return AIMessage(content='{"queries":["退货资格限制","退货期限和例外"]}')
        if self.kind == "assessment":
            return AIMessage(content=json.dumps({"verdict": self.owner.assessment_verdict,
                "explanation": "依据政策，可以申请并等待审核[1]。" if self.owner.assessment_verdict == "eligible" else "请确认商品状态[1]。",
                "missing_facts": [] if self.owner.assessment_verdict != "needs_clarification" else ["拆封状态"]}, ensure_ascii=False))
        if self.kind == "classifier":
            if self.owner.fail_classifier:
                raise RuntimeError("classifier unavailable")
            return AIMessage(
                content=json.dumps(
                    {
                        "intent": self.owner.intents.pop(0)
                        if self.owner.intents
                        else "闲聊"
                        if match_chitchat(messages[-1].content)
                        else "退款退货",
                        "confidence": 0.99,
                    },
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
def router_settings(tmp_path):
    from pathlib import Path

    from mewhelp.ch06.evaluation import model_hash, runtime_hash, verify_dataset

    path = tmp_path / "router-calibration.json"
    path.write_text(
        json.dumps(
            {
                "model_hash": model_hash(),
                "understanding_hash": runtime_hash("understanding"),
                "intent_hash": runtime_hash("intents"),
                "dataset_hash": verify_dataset(Path("eval/ch06"))["dataset_hash"],
                "intent_min_confidence": 0.6,
                "cascade_upgrade_threshold": 0.8,
                "sample_count": 32,
            }
        ),
        encoding="utf-8",
    )
    return Ch06Settings(calibration_path=path)


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
    session_factory,
    checkpoint_settings,
    model_factory,
    strong_evidence,
    monkeypatch,
    router_settings,
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
        router_settings=router_settings,
        router_model_factory=model_factory.router,
    ) as runtime:
        yield runtime


@pytest.fixture
def knowledge_request():
    return TurnRequest(message="七天无理由退货政策是什么", session_id="knowledge-test")
