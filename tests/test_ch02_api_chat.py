"""POST /ch02/chat/stream 的 SSE 契约。"""

import json

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
from tests.sse_utils import parse_sse


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
def client(session_factory, monkeypatch):
    # 依赖覆盖:路由拿到的 session 工厂指向内存库
    app = FastAPI()
    app.include_router(ch02_api.router)
    app.dependency_overrides[ch02_api.get_session_factory] = lambda: session_factory
    return TestClient(app)


def patch_model(monkeypatch, model):
    monkeypatch.setattr(service, "get_chat_model", lambda **kw: model)
    return model


def post(client, body):
    return client.post("/ch02/chat/stream", json=body)


def test_event_sequence_with_tools(client, monkeypatch):
    patch_model(monkeypatch, FakeToolChatModel(rounds=[
        tool_call_chunks("query_logistics", '{"order_id": "1001"}'),
        text_chunks("您的包裹已到杭州。"),
    ]))
    resp = post(client, {"session_id": "s1", "message": "订单 1001 的物流到哪了"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(resp.text)
    assert events[0][0] == "session"
    payload = json.loads(events[0][1])
    assert payload["session_id"] == "s1"
    assert payload["resumed"] is False

    names = [e for e, _ in events]
    assert names.count("tool") == 2
    assert names[-1] == "done"

    tools = [json.loads(d) for e, d in events if e == "tool"]
    assert [t["phase"] for t in tools] == ["start", "end"]
    assert tools[0]["name"] == "query_logistics"
    assert tools[0]["args"] == {"order_id": "1001"}
    assert tools[1]["ok"] is True

    tokens = [json.loads(d)["text"] for e, d in events if e == "token"]
    assert "".join(tokens) == "您的包裹已到杭州。"


def test_session_events_carry_resumed_true_on_the_second_turn(client, monkeypatch):
    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("A"), text_chunks("B")]))
    post(client, {"session_id": "s1", "message": "第一问"})
    second = parse_sse(post(client, {"session_id": "s1", "message": "第二问"}).text)

    assert json.loads(second[0][1])["resumed"] is True


def test_finish_reason_is_still_stop(client, monkeypatch):
    """ch01 的如实声明继续有效:这个字段恒为 "stop",不代表真实截断状态。"""
    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("您好")]))
    events = parse_sse(post(client, {"session_id": "s1", "message": "在吗"}).text)
    assert json.loads(events[-1][1])["finish_reason"] == "stop"


def test_tool_frame_on_a_failed_tool_carries_ok_false(client, monkeypatch):
    """工具失败不打断整轮 —— 模型照样作答,tool 帧的 end 带 ok=false。"""
    patch_model(monkeypatch, FakeToolChatModel(rounds=[
        tool_call_chunks("query_stock", '{"sku": "1"}'),
        text_chunks("抱歉,我没查到这件商品的库存。"),
    ]))
    events = parse_sse(post(client, {"session_id": "s1", "message": "有货吗"}).text)

    ends = [json.loads(d) for e, d in events if e == "tool" and json.loads(d)["phase"] == "end"]
    assert ends[0]["ok"] is False
    assert events[-1][0] == "done"          # 整轮没断


def test_upstream_failure_ends_with_an_error_frame_not_a_broken_stream(client, monkeypatch):
    from langchain_core.outputs import ChatGenerationChunk

    class Boom(FakeToolChatModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            # 必须吐 ChatGenerationChunk,不是裸的 AIMessageChunk:`_astream` 是
            # **内层**钩子,`BaseChatModel.astream` 才是把它翻成 AIMessageChunk 的那层。
            # 吐错类型会在 pydantic 里炸成 "'AIMessageChunk' object has no attribute
            # 'message'" —— 那条报错完全看不出是测试自己写错了。
            yield ChatGenerationChunk(message=text_chunks("部分")[0])
            raise RuntimeError("上游断了")

    patch_model(monkeypatch, Boom(rounds=[]))
    events = parse_sse(post(client, {"session_id": "s1", "message": "在吗"}).text)

    assert events[0][0] == "session"
    tokens = [json.loads(d)["text"] for e, d in events if e == "token"]
    assert "".join(tokens) == "部分"          # 已推出的 token 保留
    assert events[-1][0] == "error"
    payload = json.loads(events[-1][1])
    assert payload["code"] == "upstream_error"
    assert "上游断了" in payload["message"]


def test_empty_completion_is_coded_apart(client, monkeypatch):
    patch_model(monkeypatch, FakeToolChatModel(rounds=[
        tool_call_chunks("query_logistics", '{"order_id": "1001"}'),
        text_chunks("   "),
    ]))
    events = parse_sse(post(client, {"session_id": "s1", "message": "订单 1001 的物流"}).text)

    assert events[-1][0] == "error"
    assert json.loads(events[-1][1])["code"] == "empty_completion"


@pytest.mark.parametrize(
    "message",
    [
        pytest.param("", id="空串"),
        pytest.param("   ", id="只有空白"),
        # 零宽字符用 chr() 拼:既不直接写进源码,也不写成转义序列。
        # 前者肉眼与空串无异,一次不经意的保存就能把它抹掉;后者会被工具链解回真字符。
        # 两种写法都会让这一条静静地退化成"空串"的副本 —— 测试照绿,零宽这条规则没人守。
        pytest.param(
            "".join(chr(c) for c in (0x200B, 0x200C, 0x200D, 0xFEFF)),
            id="只有零宽字符",
        ),
    ],
)
def test_blank_message_is_rejected_before_spending_an_upstream_call(client, monkeypatch, message):
    """ch01 的规则照搬:空白在**花掉上游调用之前**拒掉。"""
    called = False

    def spy(**kw):
        nonlocal called
        called = True
        return FakeToolChatModel(rounds=[])

    monkeypatch.setattr(service, "get_chat_model", spy)
    assert post(client, {"session_id": "s1", "message": message}).status_code == 422
    assert called is False


@pytest.mark.parametrize(
    "session_id",
    [
        pytest.param("", id="空串"),
        pytest.param("   ", id="只有空白"),
    ],
)
def test_blank_session_id_is_rejected_not_treated_as_absent(client, monkeypatch, session_id):
    called = False

    def spy(**kw):
        nonlocal called
        called = True
        return FakeToolChatModel(rounds=[])

    monkeypatch.setattr(service, "get_chat_model", spy)
    assert post(client, {"session_id": session_id, "message": "在吗"}).status_code == 422
    assert called is False


def test_explicit_null_session_id_is_treated_as_absent(client, monkeypatch):
    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("在的")]))
    events = parse_sse(post(client, {"session_id": None, "message": "在吗"}).text)

    assert events[0][0] == "session"
    assert json.loads(events[0][1])["session_id"]


def test_unknown_field_is_rejected(client, monkeypatch):
    """`extra="forbid"` —— 把 `message` 拼错时不能静默丢掉。"""
    assert post(client, {"session_id": "s1", "message": "在吗", "whatever": 1}).status_code == 422


def test_user_id_defaults_to_the_placeholder(client, monkeypatch):
    """user_id 缺省即 demo-user(spec §6.4 的占位)。"""
    from sqlalchemy import select

    from mewhelp.db.models import Conversation

    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("您好")]))
    post(client, {"session_id": "s1", "message": "在吗"})

    # 覆盖项是**依赖本身**(`lambda: session_factory`),调一次拿到的是工厂,
    # 再调一次才拿到 Session —— 少一次调用会把 `with` 用在函数对象上。
    factory = client.app.dependency_overrides[ch02_api.get_session_factory]()
    with factory() as s:
        assert s.scalars(select(Conversation)).one().user_id == "demo-user"


def test_blank_user_id_is_rejected(client, monkeypatch):
    assert post(client, {"session_id": "s1", "user_id": "  ", "message": "在吗"}).status_code == 422
