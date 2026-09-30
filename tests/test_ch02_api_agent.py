"""POST /ch02/agent —— 程序化 / eval / curl 用的非流式出口。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mewhelp.ch02 import api as ch02_api
from mewhelp.ch02 import service
from mewhelp.db.base import Base
from mewhelp.db.seed import seed
from tests.fakes import FakeToolChatModel, text_chunks, tool_call_chunks


@pytest.fixture(autouse=True)
def isolated_query_understanding(monkeypatch):
    from tests.fakes import patch_query_understanding
    patch_query_understanding(monkeypatch)


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        seed(s)
        s.commit()
    return lambda: Session(engine)


@pytest.fixture
def client(session_factory):
    app = FastAPI()
    app.include_router(ch02_api.router)
    app.dependency_overrides[ch02_api.get_session_factory] = lambda: session_factory
    return TestClient(app)


def patch_model(monkeypatch, model):
    monkeypatch.setattr(service, "get_chat_model", lambda **kw: model)
    return model


def test_returns_answer_and_the_full_tool_trace(client, monkeypatch):
    """契约里最要紧的三个字段:answer / tool_calls / tool_results。"""
    patch_model(monkeypatch, FakeToolChatModel(rounds=[
        tool_call_chunks("query_logistics", '{"order_id": "1001"}'),
        text_chunks("您的包裹已到杭州。"),
    ]))
    resp = client.post("/ch02/agent", json={"session_id": "s1", "message": "订单 1001 的物流到哪了"})
    body = resp.json()

    assert resp.status_code == 200
    assert body["answer"] == "您的包裹已到杭州。"
    assert body["session_id"] == "s1"
    assert body["resumed"] is False
    assert [c["name"] for c in body["tool_calls"]] == ["query_logistics"]
    assert body["tool_calls"][0]["args"] == {"order_id": "1001"}
    assert body["tool_results"][0]["ok"] is True
    assert body["tool_results"][0]["name"] == "query_logistics"


def test_a_plain_turn_returns_empty_trace(client, monkeypatch):
    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("您好")]))
    body = client.post("/ch02/agent", json={"session_id": "s1", "message": "你好"}).json()

    assert body["answer"] == "您好"
    assert body["tool_calls"] == []
    assert body["tool_results"] == []


def test_a_failed_tool_is_reported_as_ok_false_not_a_5xx(client, monkeypatch):
    patch_model(monkeypatch, FakeToolChatModel(rounds=[
        tool_call_chunks("query_product", '{"sku": "1"}'),
        text_chunks("抱歉,没查到。"),
    ]))
    resp = client.post("/ch02/agent", json={"session_id": "s1", "message": "有货吗"})

    assert resp.status_code == 200
    assert resp.json()["tool_results"][0]["ok"] is False


def test_upstream_failure_is_502(client, monkeypatch):
    class Boom(FakeToolChatModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            # 空循环只为让本函数是 async generator 而不是协程 —— 上游在
            # 第一个分片之前就断了,所以这里一个 chunk 都不吐。
            for _ in ():
                yield
            raise RuntimeError("上游断了")

    patch_model(monkeypatch, Boom(rounds=[]))
    resp = client.post("/ch02/agent", json={"session_id": "s1", "message": "在吗"})

    assert resp.status_code == 502


def test_empty_answer_is_502(client, monkeypatch):
    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("  ")]))
    assert client.post("/ch02/agent", json={"session_id": "s1", "message": "在吗"}).status_code == 502


def test_validation_is_422(client):
    assert client.post("/ch02/agent", json={"message": "  "}).status_code == 422
    assert client.post("/ch02/agent", json={"message": "在吗", "x": 1}).status_code == 422


def test_conversation_id_is_returned(client, monkeypatch):
    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("您好")]))
    body = client.post("/ch02/agent", json={"session_id": "s1", "message": "你好"}).json()
    assert isinstance(body["conversation_id"], int)
