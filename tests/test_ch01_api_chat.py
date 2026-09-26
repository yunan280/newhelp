"""SSE 对话接口的测试 —— 假模型 + TestClient,不联网。"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk

from mewhelp.ch01 import service
from mewhelp.ch01.api import router
from mewhelp.memory import SessionStore
from tests.sse_utils import parse_sse


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    service.store = SessionStore()
    return TestClient(app)


def patch_model(monkeypatch, *replies: str):
    fake = GenericFakeChatModel(messages=iter([AIMessage(content=r) for r in replies]))
    monkeypatch.setattr(service, "get_chat_model", lambda **kw: fake)
    return fake


def post(client, body):
    return client.post("/ch01/chat/stream", json=body)


def test_event_sequence_is_session_then_tokens_then_done(client, monkeypatch):
    patch_model(monkeypatch, "您好 一般 四十八 小时 内 发货")
    resp = post(client, {"message": "几点发货?"})

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    events = parse_sse(resp.text)
    if not events:
        pytest.fail(f"SSE 解析为空,原始响应体:\n{resp.text!r}")

    assert events[0][0] == "session"
    assert events[-1][0] == "done"

    tokens = [json.loads(d)["text"] for e, d in events if e == "token"]
    assert "".join(tokens) == "您好 一般 四十八 小时 内 发货"


def test_session_event_is_emitted_even_when_client_sends_one(client, monkeypatch):
    patch_model(monkeypatch, "好的")
    events = parse_sse(post(client, {"session_id": "mine", "message": "在吗"}).text)
    assert json.loads(events[0][1])["session_id"] == "mine"


def test_generated_session_id_is_returned_and_reusable(client, monkeypatch):
    patch_model(monkeypatch, "第一轮", "第二轮")

    first = parse_sse(post(client, {"message": "第一个问题"}).text)
    session_id = json.loads(first[0][1])["session_id"]
    assert session_id

    post(client, {"session_id": session_id, "message": "第二个问题"})

    # 刻意读私有字段:TestClient 自己管事件循环,这里没有 await 的余地。
    # 断言的是"两轮都落进了同一个会话"。
    assert len(service.store._sessions[session_id]) == 4


@pytest.mark.parametrize(
    "message",
    [
        pytest.param("", id="空串"),
        pytest.param("   ", id="只有空白"),
    ],
)
def test_blank_message_is_rejected_before_calling_the_model(client, monkeypatch, message):
    """Review Focus #1:空消息必须在花掉一次上游调用之前就被拒。

    `只有空白` 那两档是 `Field(min_length=1)` 拦不住的 —— 长度够,但不是内容。
    只测空串的话,把 field_validator 删掉照样绿。

    `called` 是这条测试的真正主力:`422` 只能证明请求被拒,**证明不了没花上游调用**。
    把校验挪到路由函数体内(先建模型再校验)时,422 依旧,只有这里会红。
    """
    called = False

    def spy(**kw):
        nonlocal called
        called = True
        return GenericFakeChatModel(messages=iter([AIMessage(content="x")]))

    monkeypatch.setattr(service, "get_chat_model", spy)
    resp = post(client, {"message": message})

    assert resp.status_code == 422
    assert called is False


def test_model_failure_mid_stream_keeps_tokens_and_ends_with_error_event(client, monkeypatch):
    """Review Focus #2:已推出的 token 保留,补一个 error 事件收尾,不断连。"""

    class Boom:
        async def astream(self, messages):
            yield AIMessageChunk(content="部分内容")
            raise RuntimeError("上游断了")

    monkeypatch.setattr(service, "get_chat_model", lambda **kw: Boom())
    resp = post(client, {"message": "在吗"})
    events = parse_sse(resp.text)

    assert events[0][0] == "session"
    tokens = [json.loads(d)["text"] for e, d in events if e == "token"]
    assert "".join(tokens) == "部分内容"
    assert events[-1][0] == "error"
    payload = json.loads(events[-1][1])
    assert "上游断了" in payload["message"]
    # 上游断流是"什么都可能发生"的那一类,不能冒充空回复
    assert payload["code"] == "upstream_error"


def test_empty_completion_is_coded_apart_from_upstream_error(client, monkeypatch):
    """空回复与上游断流必须是两个可区分的 code。

    两者此前都抛裸 RuntimeError,api 层只能靠正则匹配 message 文案来分。
    带上 code 之后,客户端不必猜中文文案。

    这条的判别力全在 `== "empty_completion"` 上:把 mapping 写成恒返回
    `"upstream_error"`,只有本用例会红(上面那条 Boom 用例照样绿)。
    """

    class Silent:
        """上游一个块都不给 —— 空回复的典型形态。"""

        async def astream(self, messages):
            return
            yield  # 让它成为异步生成器而非协程函数

    monkeypatch.setattr(service, "get_chat_model", lambda **kw: Silent())
    events = parse_sse(post(client, {"message": "在吗"}).text)

    assert events[0][0] == "session"
    assert events[-1][0] == "error"
    payload = json.loads(events[-1][1])
    assert payload["code"] == "empty_completion"
    # message 字段照旧是异常原文,别为了加 code 把人类可读的部分挤掉
    assert "没有产出任何内容" in payload["message"]
