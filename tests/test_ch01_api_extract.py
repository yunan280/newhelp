"""结构化抽取接口的测试 —— 假结构化模型,不联网。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from mewhelp.ch01 import service
from mewhelp.ch01.api import router
from mewhelp.ch01.schemas import AfterSalesIntent, AfterSalesTicket, ExpectedSolution


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def patch_structured(monkeypatch, result):
    """把 service 里取的结构化模型换成固定返回值的假对象。"""

    class FakeStructured:
        async def ainvoke(self, messages):
            return result

    monkeypatch.setattr(service, "get_structured_model", lambda *a, **kw: FakeStructured())
    return FakeStructured


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
    """Review Focus #4:function_calling 下模型可能压根不调工具,返回 None。"""
    patch_structured(monkeypatch, None)
    resp = client.post("/ch01/extract", json={"description": "嗯"})
    assert resp.status_code == 422
    assert "结构化" in resp.json()["detail"]


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
    called = False

    class Spy:
        async def ainvoke(self, messages):
            nonlocal called
            called = True  # 隐式返回 None —— 与"模型不调工具"同形

    monkeypatch.setattr(service, "get_structured_model", lambda *a, **kw: Spy())
    resp = client.post("/ch01/extract", json={"description": description})

    assert resp.status_code == 422
    assert called is False


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
            return AfterSalesTicket(
                intent=AfterSalesIntent.exchange,
                expected_solution=ExpectedSolution.unspecified,
            )

    monkeypatch.setattr(service, "get_structured_model", lambda *a, **kw: Capturing())
    resp = client.post("/ch01/extract", json={"description": "\t  订单 20240915001 想换大一码  "})

    assert resp.status_code == 200
    # EXTRACT_PROMPT 渲染出 [system] + 本轮 human;这里只关心后者
    humans = [m for m in seen[0] if m.type == "human"]
    assert [m.content for m in humans] == ["\t  订单 20240915001 想换大一码  "]
