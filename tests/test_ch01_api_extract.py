"""结构化抽取接口的测试 —— 假结构化模型,不联网。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from mewhelp.ch01 import service
from mewhelp.ch01.api import router
from mewhelp.ch01.schemas import AfterSalesIntent, AfterSalesTicket, ExpectedSolution


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def patch_structured(monkeypatch, result, *, raw="", parsing_error=None, captured=None):
    """把 service 里取的结构化模型换成固定返回值的假对象。

    假对象返回的是 `include_raw=True` 的形状(实测于 langchain-core 1.6.5 的
    `{"raw", "parsed", "parsing_error"}`),**别改成裸返回值** —— 那个形状是 service
    与 llm 之间的真实契约,假对象跟着它走,漏传 `include_raw` 这类坏法才看得见。
    `captured` 传字典时,把工厂收到的 kwargs 记进去。
    """

    class FakeStructured:
        async def ainvoke(self, messages):
            return {
                "raw": AIMessage(content=raw),
                "parsed": result,
                "parsing_error": parsing_error,
            }

    def factory(schema, **kwargs):
        if captured is not None:
            captured.update(kwargs)
        return FakeStructured()

    monkeypatch.setattr(service, "get_structured_model", factory)


def spy_structured(monkeypatch):
    """只记录"有没有被调用"的假结构化模型 —— 返回记录列表,空列表即没被调用。

    被调用时给一张**合法**的票:这样"本该被拒的请求却走到了模型"会以 200 现形,
    不会被"模型返回 None 于是也 422"顺手遮住 —— 状态码在这里本来就不可信。
    """

    called: list = []

    class Spy:
        async def ainvoke(self, messages):
            called.append(True)
            return {
                "raw": AIMessage(content="好的"),
                "parsed": AfterSalesTicket(
                    intent=AfterSalesIntent.refund,
                    expected_solution=ExpectedSolution.unspecified,
                ),
                "parsing_error": None,
            }

    monkeypatch.setattr(service, "get_structured_model", lambda *a, **kw: Spy())
    return called


def test_extract_returns_the_ticket_as_json(client, monkeypatch):
    patch_structured(
        monkeypatch,
        AfterSalesTicket(
            order_id="20240915001",
            intent=AfterSalesIntent.exchange,
            expected_solution=ExpectedSolution.exchange,
            reason="尺码不合适",
        ),
    )
    resp = client.post("/ch01/extract", json={"description": "订单 20240915001 想换大一码"})

    assert resp.status_code == 200
    assert resp.json() == {
        "order_id": "20240915001",
        "intent": "换货",
        "expected_solution": "换货",
        "reason": "尺码不合适",
    }


def test_null_fields_are_preserved_in_the_response(client, monkeypatch):
    patch_structured(
        monkeypatch,
        AfterSalesTicket(
            intent=AfterSalesIntent.refund,
            expected_solution=ExpectedSolution.unspecified,
        ),
    )
    body = client.post("/ch01/extract", json={"description": "能退吗"}).json()
    assert body["order_id"] is None
    assert body["reason"] is None
    assert body["expected_solution"] == "未提及"


def test_model_returning_none_yields_422_not_500(client, monkeypatch):
    """Review Focus #4:function_calling 下模型可能压根不调工具,`parsed` 是 None。"""
    patch_structured(monkeypatch, None)
    resp = client.post("/ch01/extract", json={"description": "嗯"})
    detail = resp.json()["detail"]

    assert resp.status_code == 422
    assert "结构化" in detail
    # raw 为空时 detail 不能停在"模型原始输出:"的空冒号后面 —— 空串恰恰是最需要解释的失败。
    # 这条盯的是"既没文本、也没调工具"那条兜底分支(另一条由下面那条 parsing_error 用例守),
    # 它此前没有任何断言:把这条分支改成 `return content`(给出空串),别的用例全绿。
    assert "没有输出任何文本" in detail


def test_422_detail_carries_the_model_raw_output(client, monkeypatch):
    """spec §十:模型不调工具时,422 要带上**原始返回文本**,便于定位。

    固定一句话的 detail 只留给人猜;模型这一轮的原话才说得清"它到底说了什么"。
    顺带钉住 `include_raw=True` 真的传到了工厂 —— 假对象无论收到什么 kwargs 都照返回
    那个形状,所以"只断言 detail 里有 raw"是守不住漏传的,必须断言 kwargs 本身。
    """
    raw_text = "我看不懂你的意思,你要退的是哪一件?"
    captured: dict = {}
    patch_structured(monkeypatch, None, raw=raw_text, captured=captured)

    resp = client.post("/ch01/extract", json={"description": "嗯"})

    assert resp.status_code == 422
    assert raw_text in resp.json()["detail"]
    # 原来那句人类可读的说明不能被 raw 挤掉:客户端要能区分"我该重试"和"模型坏了"
    assert "结构化" in resp.json()["detail"]
    assert captured["include_raw"] is True


def test_long_raw_output_is_truncated_and_says_so(client, monkeypatch):
    """detail 是发给客户端的:坏掉的模型可能吐出一大段,不能原样转发。

    截断必须**说出来** —— 静默截断会让人以为自己看到的就是全部,
    恰好在"便于定位"这个字段存在的唯一目的上骗人。
    """
    raw_text = "坏掉的模型开始复读:" + "很抱歉" * 300 + "【尾巴标记】"
    patch_structured(monkeypatch, None, raw=raw_text)

    detail = client.post("/ch01/extract", json={"description": "嗯"}).json()["detail"]

    assert raw_text[:500] in detail
    assert "【尾巴标记】" not in detail  # 尾巴确实被切掉了
    assert "已截断" in detail
    assert str(len(raw_text)) in detail  # 说清原始有多长
    # 上面四条只钉住"切了",钉不住"切到多短":把上限从 500 改成 800 它们照样绿
    # (前缀仍在前 800 里,尾巴仍在 916 之外)。这条钉的是**量级** ——
    # 嵌入的 raw 是 O(500) 的定长前缀,不跟着 raw 长度一起长,那才是设上限的目的。
    assert len(detail) < 700


def test_raw_output_of_exactly_the_limit_is_not_called_truncated(client, monkeypatch):
    """恰好等于上限的 raw 要**整段**放行,不能挂上"已截断"的牌子。

    上面那条的 916 字与最前面几条的十几个字,都在 `len(raw) <= LIMIT` 这个判断的
    两端之外:把 `<=` 写成 `<`,恰好 500 字的 raw 会被"截断"——前缀其实一个字节没少,
    牌子却说"已截断,原始输出共 500 字符"。这正是那个字段存在的唯一目的
    (说清到底发生了什么)上的一次谎报:客户端会以为还有内容没看到。
    """
    raw_text = "很" * 500
    patch_structured(monkeypatch, None, raw=raw_text)

    detail = client.post("/ch01/extract", json={"description": "嗯"}).json()["detail"]

    assert raw_text in detail  # 一个字都没丢
    assert "已截断" not in detail  # 也就不能说自己截断了


def test_empty_raw_output_reports_the_parsing_error_instead(client, monkeypatch):
    """raw 为空时不能给一个空串 —— 那恰恰是最需要解释的一种失败。

    模型调了工具、参数却过不了 schema 时,`raw.content` 就是空的(实测于
    langchain-core 1.6.5),这时 detail 要说清"调了工具但参数不合法",并带上报错本身。
    """
    patch_structured(monkeypatch, None, raw="", parsing_error=ValueError("intent 不是合法取值"))

    detail = client.post("/ch01/extract", json={"description": "嗯"}).json()["detail"]

    assert "intent 不是合法取值" in detail
    assert "不满足 schema" in detail


def test_unknown_field_is_rejected_instead_of_silently_dropped(client, monkeypatch):
    """这条接口不认的字段必须 422,不能静默丢掉。

    把聊天接口的 body 顺手复用过来(`{"description": ..., "session_id": "abc"}`)
    是很自然的调用方式。静默吞掉 `session_id` 的话,客户端以为自己续上了会话
    —— 而这条接口根本没有会话 —— 两边对不上却全程无报错。
    """
    called = spy_structured(monkeypatch)
    resp = client.post("/ch01/extract", json={"description": "能退吗", "session_id": "abc"})

    assert resp.status_code == 422
    assert called == []  # 在花掉上游调用之前就拒掉


@pytest.mark.parametrize(
    "description",
    [
        pytest.param("", id="空串"),
        pytest.param("   ", id="只有空白"),
    ],
)
def test_blank_description_is_rejected_before_calling_the_model(client, monkeypatch, description):
    """Review Focus #4 的另一半:空输入不该白花一次上游调用。

    `只有空白` 那一档是 `Field(min_length=1)` 拦不住的 —— 长度够,但不是内容,
    而且照常花掉一次真实的上游调用。只测空串的话,把 `field_validator` 删掉照样绿。

    `called` 是这条测试的真正主力:`422` 只能证明请求被拒,**证明不了没花上游调用** ——
    空串那一档就算把校验全删了,路由里的 `ticket is None` 分支也会给出 422,
    于是只有 `called` 会红。别指望状态码能守住它。
    """
    called = spy_structured(monkeypatch)
    resp = client.post("/ch01/extract", json={"description": description})

    assert resp.status_code == 422
    assert called == []


def test_description_reaches_the_prompt_verbatim(client, monkeypatch):
    """描述必须一个字节不动地进 prompt —— 假模型不管收到什么都照答,这层得单独钉。

    前四条用例的假模型都无视 `messages`,于是"描述压根没进 prompt"这种坏法
    (service 里把 `description=""` 写死)**5 条用例全绿**,端点照旧返回一个看着
    完全正常的工单,只是这份工单跟用户说的那段话毫无关系 —— 无异常、无报错、无痕迹。
    与 chat 侧 `test_message_whitespace_reaches_the_model_verbatim` 同形、同一个洞。
    """
    seen: list[list] = []

    class Capturing:
        """把送进模型的 messages 原样记下来。"""

        async def ainvoke(self, messages):
            seen.append(messages)
            return {
                "raw": AIMessage(content="好的"),
                "parsed": AfterSalesTicket(
                    intent=AfterSalesIntent.exchange,
                    expected_solution=ExpectedSolution.unspecified,
                ),
                "parsing_error": None,
            }

    monkeypatch.setattr(service, "get_structured_model", lambda *a, **kw: Capturing())
    resp = client.post("/ch01/extract", json={"description": "\t  订单 20240915001 想换大一码  "})

    assert resp.status_code == 200
    # EXTRACT_PROMPT 渲染出 [system] + 本轮 human;这里只关心后者
    humans = [m for m in seen[0] if m.type == "human"]
    assert [m.content for m in humans] == ["\t  订单 20240915001 想换大一码  "]
