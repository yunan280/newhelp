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


def test_all_intent_members_have_nonempty_chinese_labels():
    for member in AfterSalesIntent:
        assert member.value.strip()
        assert member.value != member.name


def test_enum_member_counts_match_the_spec():
    assert len(AfterSalesIntent) == 9
    assert len(ExpectedSolution) == 8


def test_schema_is_json_serialisable():
    t = AfterSalesTicket(**valid_payload())
    dumped = t.model_dump(mode="json")
    assert dumped["intent"] == "退款"
    assert dumped["expected_solution"] == "全额退款"
