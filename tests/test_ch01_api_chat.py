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
    # done 的 payload 也要断言:只断事件名的话,`data={}` 照样绿 —— 客户端拿到的
    # 收尾信号里没有任何可判读的字段,却没有任何测试会响。
    assert json.loads(events[-1][1])["finish_reason"] == "stop"

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

    `只有空白` 那一档是 `Field(min_length=1)` 拦不住的 —— 长度够,但不是内容。
    只测空串的话,把 field_validator 删掉照样绿。

    `called` 是这条测试的真正主力:`422` 只能证明请求被拒,**证明不了没花上游调用**。
    能不能红?实测**不能**靠"把校验挪进路由函数体":生成器里、首个 yield 之前
    raise HTTPException,客户端收到的是 200 加空 body 而不是 422,会先红在状态码上。
    真正的变异是把模型改成 `Depends` 注入 —— 依赖先于 body 校验解析,于是 422 依旧、
    模型却已经被建出来了,这条断言这才红。别指望状态码能守住它。
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


def test_message_whitespace_reaches_the_model_verbatim(client, monkeypatch):
    """`_reject_blank_message` 的 docstring 承诺"只校验,不 strip" —— 这条守那个承诺。

    请求里的首尾空白必须**一个字节不动**地到模型手里。把校验器改成
    `return value.strip()` 时,拒空白的能力一点没丢(上面那条用例照样绿),
    改写掉的只有用户真正问的那句话 —— 用户问的和他被回答的不再是同一条,
    而且服务端写回历史的也是被改写过的那条。
    """
    seen: list[list] = []

    class Capturing:
        """把送进模型的 messages 原样记下来。"""

        async def astream(self, messages):
            seen.append(messages)
            yield AIMessageChunk(content="好的")

    monkeypatch.setattr(service, "get_chat_model", lambda **kw: Capturing())
    resp = post(client, {"message": "\t  几点发货?  "})

    assert resp.status_code == 200
    # CHAT_PROMPT 渲染出 [system] + history,再拼本轮 HumanMessage;这里只关心后者
    humans = [m for m in seen[0] if m.type == "human"]
    assert [m.content for m in humans] == ["\t  几点发货?  "]


def test_unknown_field_is_rejected_instead_of_silently_dropped(client, monkeypatch):
    """聊天接口不认的字段必须 422,不能静默丢掉。

    把 `message` 拼错、或从别处抄了一个 body 过来时,静默吞字段意味着**用户那句话
    根本没进模型**,而服务端照常回一句像样的答复 —— 没有异常、没有报错、没有痕迹。

    与 extract 侧那条用例同形但**各自独立**:两个请求模型各有一行
    `model_config = ConfigDict(extra="forbid")`,没有共用基类 —— 所以这里删掉
    chat 那行,extract 那条照样绿,只有本用例会红。
    """
    called = False

    def spy(**kw):
        nonlocal called
        called = True
        return GenericFakeChatModel(messages=iter([AIMessage(content="x")]))

    monkeypatch.setattr(service, "get_chat_model", spy)
    resp = post(client, {"message": "在吗", "whatever": 1})

    assert resp.status_code == 422
    assert called is False  # 在花掉上游调用之前就拒掉


@pytest.mark.parametrize(
    "session_id",
    [
        pytest.param("", id="空串"),
        pytest.param("   ", id="只有空白"),
    ],
)
def test_blank_session_id_is_rejected_not_treated_as_absent(client, monkeypatch, session_id):
    """空白的 session_id 必须 422,不能当成"没传"。

    `req.session_id or uuid4().hex` 会把 `""` 吞掉,于是**每一轮都开一个新会话**:
    客户端每轮都拿回一个看着完全正常的新 id,上下文却整段丢,验收标准②
    ("连问两轮,第二轮要接上第一轮")在无任何报错的情况下失效。
    JS 里 `""` 是 falsy、变量未赋值读作空串,踩中的成本极低。

    与空消息同一类:都在**花掉上游调用之前**拒掉,所以 `called` 也一起断言。
    """
    called = False

    def spy(**kw):
        nonlocal called
        called = True
        return GenericFakeChatModel(messages=iter([AIMessage(content="x")]))

    monkeypatch.setattr(service, "get_chat_model", spy)
    resp = post(client, {"session_id": session_id, "message": "在吗"})

    assert resp.status_code == 422
    assert called is False


def test_explicit_null_session_id_is_treated_as_absent(client, monkeypatch):
    """显式传 `"session_id": null` 等同"没传":服务端生成一个,不能 500。

    `session_id` 是 Optional,校验器必须容得下 None。它走的是**显式传值**这条路 ——
    字段缺省时 pydantic 压根不调校验器(默认 `validate_default=False`),所以
    "不带 session_id"的那几条用例盖不到这里:把 `_reject_blank` 里 `value is None`
    那两行删掉,缺省路径一切照旧,**只有这条**会红在
    `AttributeError: 'NoneType' object has no attribute 'strip'`。

    这也是 JS 客户端很常见的写法:JSON.stringify 一个未赋值的变量给的正是 null,
    而它既不该被当成空白拒掉,也不该把 null 当 id 用。
    """
    patch_model(monkeypatch, "在的")
    events = parse_sse(post(client, {"session_id": None, "message": "在吗"}).text)

    assert events[0][0] == "session"
    generated = json.loads(events[0][1])["session_id"]
    assert generated  # 服务端生成了 id,而不是把 null 原样回给客户端
    assert len(service.store._sessions[generated]) == 2  # 这一轮真的落进了那个会话


def test_whitespace_bearing_session_id_is_echoed_and_reused_verbatim(client, monkeypatch):
    """会话 id 是不透明串:非空白的原样放行 —— 不 strip,也不重新生成。

    与空消息那条同形:`_reject_blank_session_id` 的 docstring 承诺"非空白的原样放行,
    **不 strip**",但在此之前唯一走到的非空白值是 `"mine"` —— 没有空白可丢,
    把 `return value` 改成 `return value.strip()` 全套用例照样绿。这条补那个空洞。

    断言的是**可观测契约**,不是校验器的返回值:

    1. `session` 事件逐字节回显客户端给的 id;
    2. 拿同一个 id 再发一轮,两轮落进同一个会话(写侧);
    3. **第二轮真的看得见第一轮**(读侧)—— 见下。

    第 3 条是这里最要紧的一条,也是最容易被漏掉的。只断 1、2 的话,
    把 `service.py` 的 `store.get(session_id)` 改成 `store.get(session_id.strip())`
    全场照样绿:写进去的键仍是原样的 id(所以第 2 条的 store 断言仍然成立),
    读却去读了另一个键 —— `SessionStore.get` 对未知键**返回空列表而不是抛异常**,
    于是第二轮只拿到 `[system, 本轮提问]`,第一轮整段丢,无异常、无报错、无任何痕迹。
    这正是验收标准②失效的那个形态,只是从写侧挪到了读侧。
    """
    seen: list[list] = []

    class Capturing:
        """记下每次调用实际收到的 messages —— 读侧的洞只有在模型这一端才看得见。"""

        async def astream(self, messages):
            seen.append(messages)
            yield AIMessageChunk(content="第一轮的回答" if len(seen) == 1 else "第二轮的回答")

    monkeypatch.setattr(service, "get_chat_model", lambda **kw: Capturing())
    sid = "  demo-session  "

    first = parse_sse(post(client, {"session_id": sid, "message": "第一个问题"}).text)
    assert json.loads(first[0][1])["session_id"] == sid

    second = parse_sse(post(client, {"session_id": sid, "message": "第二个问题"}).text)
    assert json.loads(second[0][1])["session_id"] == sid

    # 写侧:刻意读私有字段,理由同 test_generated_session_id_is_returned_and_reusable:
    # TestClient 自己管事件循环,这里没有 await 的余地。两轮 = 4 条。
    assert len(service.store._sessions[sid]) == 4

    # 读侧:第二轮送进模型的整串对话消息,逐字精确、且顺序正确。
    # 用 `==` 而不是 `in`:顺序反了、多了一条、少了一条都要红 ——
    # 顺序敏感的断言才能盖住"两条都在但前后颠倒"这种半坏状态。
    second_turn_contents = [m.content for m in seen[1]]
    # [0] 是 CHAT_PROMPT 渲染出的 SystemMessage,内容与本契约无关,故从 [1:] 起比。
    assert second_turn_contents[1:] == ["第一个问题", "第一轮的回答", "第二个问题"]


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
