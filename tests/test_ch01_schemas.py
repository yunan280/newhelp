"""结构化输出模型的测试 —— 重点是"枚举外的值必须被拒"。"""

import pytest
from pydantic import ValidationError

from mewhelp.ch01.schemas import (
    AfterSalesIntent,
    AfterSalesTicket,
    ExpectedSolution,
)


def valid_payload(**over):
    base = {
        "order_id": "20240915001",
        "intent": "退款",
        "expected_solution": "全额退款",
        "reason": "商品破损",
    }
    base.update(over)
    return base


def test_valid_payload_parses():
    t = AfterSalesTicket(**valid_payload())
    assert t.order_id == "20240915001"
    assert t.intent is AfterSalesIntent.refund
    assert t.expected_solution is ExpectedSolution.full_refund


def test_order_id_may_be_omitted():
    p = valid_payload()
    del p["order_id"]
    assert AfterSalesTicket(**p).order_id is None


def test_order_id_may_be_explicit_null():
    assert AfterSalesTicket(**valid_payload(order_id=None)).order_id is None


def test_reason_may_be_omitted():
    p = valid_payload()
    del p["reason"]
    assert AfterSalesTicket(**p).reason is None


def test_intent_outside_the_enum_is_rejected():
    with pytest.raises(ValidationError):
        AfterSalesTicket(**valid_payload(intent="我要闹了"))


def test_expected_solution_outside_the_enum_is_rejected():
    with pytest.raises(ValidationError):
        AfterSalesTicket(**valid_payload(expected_solution="随便"))


def test_intent_is_required():
    p = valid_payload()
    del p["intent"]
    with pytest.raises(ValidationError):
        AfterSalesTicket(**p)


def test_expected_solution_is_required():
    p = valid_payload()
    del p["expected_solution"]
    with pytest.raises(ValidationError):
        AfterSalesTicket(**p)


def test_intent_values_are_pinned_verbatim_in_order():
    """17 个中文字面量是模型被要求 emit 的字符串,也是 Task 10 评估集的标注基准。

    这里必须逐字 + 有序整表比对。原先的 `value.strip()` + `value != name`
    是假把关:实测 `other = "别的"`、`exchange = "换东西"` 这类替换能让整套测试
    全绿 —— 因为它们既非空、也不等于成员名。而字面量一旦被"顺手改短"
    (如「优惠券补偿」→「优惠券」),评估会静默误判,套件却不会响 ——
    测量工具本身失准是最坏的盲点。
    顺序也要钉:spec 第十节固定了成员顺序。整表比对一行同时钉住取值、顺序与数量。
    成员名同样要钉 —— reviewer 实测:把 `coupon` 改名成 `voucher` 而值一字不动,
    只看 value 的断言整套测试仍然全绿,但下游按成员名引用枚举的地方会静默失配。
    spec 第十节对成员名的约束力与取值相同,所以两条序列一起钉。
    """
    assert [m.value for m in AfterSalesIntent] == [
        "退款",
        "退货",
        "换货",
        "维修",
        "补发",
        "补偿",
        "咨询",
        "投诉",
        "其他",
    ]
    assert [m.name for m in AfterSalesIntent] == [
        "refund",
        "return_goods",
        "exchange",
        "repair",
        "reship",
        "compensation",
        "consultation",
        "complaint",
        "other",
    ]


def test_expected_solution_values_are_pinned_verbatim_in_order():
    """同 `test_intent_values_are_pinned_verbatim_in_order`:值 + 成员名,都逐字有序钉死。"""
    assert [m.value for m in ExpectedSolution] == [
        "全额退款",
        "部分退款",
        "换货",
        "维修",
        "补发",
        "优惠券补偿",
        "仅需解释",
        "未提及",
    ]
    assert [m.name for m in ExpectedSolution] == [
        "full_refund",
        "partial_refund",
        "exchange",
        "repair",
        "reship",
        "coupon",
        "explanation",
        "unspecified",
    ]


def test_enum_member_counts_match_the_spec():
    assert len(AfterSalesIntent) == 9
    assert len(ExpectedSolution) == 8


def test_schema_is_json_serialisable():
    t = AfterSalesTicket(**valid_payload())
    dumped = t.model_dump(mode="json")
    assert dumped["intent"] == "退款"
    assert dumped["expected_solution"] == "全额退款"
