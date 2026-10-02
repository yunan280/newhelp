import time
from contextlib import asynccontextmanager
from importlib import import_module
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from mewhelp.ch05.limits import AgentLimits
from mewhelp.ch05.runtime import WorkflowRuntime
from mewhelp.ch05.state import WorkflowContext, WorkflowState
from mewhelp.ch05.workflow import begin_turn, log_node
from mewhelp.ch06.orders import load_demo_order
from mewhelp.memory import SessionStore


@pytest.fixture
def session_factory(tmp_path):
    from mewhelp.db.base import Base
    engine = create_engine(f"sqlite:///{tmp_path / 'business.sqlite'}")
    Base.metadata.create_all(engine)
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@asynccontextmanager
async def open_selection_runtime(factory, path):
    try:
        selection = import_module("mewhelp.ch06.selection")
    except ImportError:
        pytest.fail("missing native selection mechanism")
    controls = SimpleNamespace(fail_once=False)
    context = WorkflowContext(factory, controls, lambda: None, AgentLimits())
    graph = StateGraph(WorkflowState, context_schema=WorkflowContext)
    async def start(state):
        return {**await begin_turn(state, context, lambda e: None),
                "intent": "物流" if "物流" in state["question"] else "退款退货",
                "route": "business" if "物流" in state["question"] else "aftersales",
                "resolved_question": state["question"], "node_trace": ["begin_turn"],
                "calls": {"classifier": 1}, "usage": {"input_tokens": 40, "output_tokens": 8}}
    async def prepare(state):
        return selection.prepare_selection(state)
    async def wait(state):
        return await selection.offer_order_selection(state, context, lambda e: None)
    async def finish(state):
        if controls.fail_once:
            controls.fail_once = False
            raise RuntimeError("transient failure after order resume")
        if state.get("selected_order_id") and time.time() - state["started_at"] > 180:
            raise TimeoutError("execution window expired")
        order = load_demo_order(state["user_id"], state["selected_order_id"]) if state.get("selected_order_id") else None
        update = {"status": "completed", "order": order.model_dump(mode="json") if order else None,
                  "answer": "已读取订单" if order else "", "node_trace": state["node_trace"] + ["finish"]}
        return {**update, **await log_node({**state, **update}, context, lambda e: None)}
    graph.add_node("begin", start)
    graph.add_node("prepare", prepare)
    graph.add_node("wait", wait)
    graph.add_node("finish", finish)
    graph.add_edge(START, "begin")
    graph.add_conditional_edges("begin", lambda s: "finish" if s["route"] == "business" else "prepare")
    graph.add_edge("prepare", "wait")
    graph.add_edge("wait", "finish")
    graph.add_edge("finish", END)
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        runtime = WorkflowRuntime(graph.compile(checkpointer=saver), context, SessionStore())
        runtime.test_controls = controls
        yield runtime


@pytest.fixture
async def selection_runtime(session_factory, tmp_path):
    async with open_selection_runtime(session_factory, tmp_path / "checkpoint.sqlite") as runtime:
        yield runtime


@pytest.fixture
def core_factory():
    from tests.ch05.conftest import ModelFactory
    return ModelFactory()


@pytest.fixture
async def core_runtime(session_factory, core_factory, tmp_path, monkeypatch):
    import json
    from pathlib import Path

    from mewhelp.ch05 import workflow
    from mewhelp.ch05.config import Ch05Settings
    from mewhelp.ch05.evidence import EvidenceEnvelope
    from mewhelp.ch05.runtime import open_runtime
    from mewhelp.ch06.config import Ch06Settings
    from mewhelp.ch06.evaluation import model_hash, runtime_hash, verify_dataset
    from mewhelp.knowledge.answering import SourceDTO
    path = tmp_path / "router.json"
    path.write_text(json.dumps({"model_hash": model_hash(), "understanding_hash": runtime_hash("understanding"),
        "intent_hash": runtime_hash("intents"), "dataset_hash": verify_dataset(Path("eval/ch06"))["dataset_hash"],
        "intent_min_confidence": .5, "cascade_upgrade_threshold": .7, "sample_count": 32}), encoding="utf-8")
    source = SourceDTO(number=1, chunk_id="1", questions="退款售后政策", answer="未拆封且期限内支持申请。",
        section_path="退款售后政策/期限", category="退款售后政策", product_category=None,
        content_hash="a" * 64, source_url="/kb/source/1")
    evidence = EvidenceEnvelope(sources=[source], scores=[.9], threshold=.5, context_budget=32000)
    async def retrieve(*a, **kw):
        return evidence
    monkeypatch.setattr(workflow, "retrieve_knowledge", retrieve)
    if hasattr(workflow, "retrieve_policy"):
        monkeypatch.setattr(workflow, "retrieve_policy", retrieve)
        monkeypatch.setattr(workflow, "load_policy_calibration", lambda c: object())
    async with open_runtime(session_factory, settings=Ch05Settings(checkpoint_path=tmp_path/"workflow.sqlite"),
        model_factory=core_factory, router_model_factory=core_factory.router,
        router_settings=Ch06Settings(calibration_path=path), rag_factory=lambda: object()) as runtime:
        runtime.test_evidence = evidence
        yield runtime
