import json
from importlib import import_module

import pytest
from langchain_core.messages import AIMessage

from mewhelp.ch05.limits import AgentLimits
from mewhelp.ch05.state import WorkflowContext


def understanding():
    try:
        return import_module("mewhelp.ch06.understanding").understand_query
    except ImportError:
        pytest.fail("missing conservative understanding")


async def resolve(question, raw, history=None, entities=None):
    class Model:
        async def ainvoke(self, messages):
            return AIMessage(
                content=json.dumps(raw, ensure_ascii=False),
                usage_metadata={"input_tokens": 40, "output_tokens": 20, "total_tokens": 60},
            )

    context = WorkflowContext(
        lambda: None,
        lambda: None,
        lambda: None,
        AgentLimits(),
        router_model_factory=lambda **kw: Model(),
    )
    return await understanding()(question, history or [], entities or [], context=context, state={})


def output(question, order_id=None, mid=None, scope="order_specific"):
    return {
        "question": question,
        "scope": scope,
        "reference_order_id": order_id,
        "reference_message_id": mid,
    }


@pytest.mark.parametrize(
    "original",
    [
        "  订单1001的K87键盘未拆封，签收7天内且不影响二次销售，能退吗？  ",
        "机械键盘支持蓝牙连接吗？",
        "订单1002如果不是人为损坏，能保修吗？",
    ],
)
async def test_rewrite_preserves_numbers_negations_and_conditions(original):
    result = await resolve(original, output("请问键盘可以退货吗？", "1001"))
    assert result.question == original


async def test_two_orders_cannot_invent_referent():
    history = [
        {"id": "a1", "role": "assistant", "content": "订单1001是键盘"},
        {"id": "a2", "role": "assistant", "content": "订单1002是耳机"},
    ]
    entities = [
        {"order_id": "1001", "product_name": "键盘", "message_id": "a1"},
        {"order_id": "1002", "product_name": "耳机", "message_id": "a2"},
    ]
    result = await resolve(
        "它能退吗", output("订单1002的耳机能退货吗？", "1002", "a2"), history, entities
    )
    assert result.trusted_order_id is None and result.question == "它能退吗"


async def test_new_session_does_not_import_old_entities():
    result = await resolve(
        "这个能退吗",
        output("订单1001的键盘能退吗？", "1001", "old"),
        entities=[{"order_id": "1001", "product_name": "键盘", "message_id": "old"}],
    )
    assert result.trusted_order_id is None and "1001" not in result.question


async def test_unique_checkpoint_entity_resolves_reference():
    result = await resolve(
        "它能退吗",
        output("订单1001的机械键盘能退货吗？", "1001", "a1"),
        [{"id": "a1", "role": "assistant", "content": "订单1001的商品为机械键盘"}],
        [{"order_id": "1001", "product_name": "机械键盘", "message_id": "a1"}],
    )
    assert result.trusted_order_id == "1001"
    assert result.question == "订单1001的机械键盘能退货吗？"
    assert result.usage.total == 60 and result.calls["understanding"] == 1


async def test_history_text_alone_cannot_supply_order_fact():
    result = await resolve(
        "它能退吗",
        output("订单1001能退货吗？", "1001", "a1"),
        [{"id": "a1", "role": "assistant", "content": "订单1001未拆封"}],
        [],
    )
    assert result.trusted_order_id is None and result.question == "它能退吗"


async def test_reference_does_not_invent_condition_or_amount():
    result = await resolve(
        "它能退吗",
        output("订单1001的机械键盘未拆封，实付1元，能退吗？", "1001", "a1"),
        [{"id": "a1", "role": "assistant", "content": "订单1001的商品为机械键盘"}],
        [{"order_id": "1001", "product_name": "机械键盘", "message_id": "a1"}],
    )
    assert result.trusted_order_id == "1001"
    assert "未拆封" not in result.question and "1元" not in result.question
    assert "1001" in result.question


async def test_price_is_not_an_explicit_order_id():
    result = await resolve("1001元的键盘能退吗？", output("订单1001能退货吗？", "1001"))
    assert result.trusted_order_id is None


async def test_reference_preserves_negation_attachment():
    result = await resolve(
        "它不是坏了，只是不想要",
        output("订单1001的机械键盘坏了，不想退", "1001", "a1"),
        [{"id": "a1", "role": "assistant", "content": "订单1001的商品为机械键盘"}],
        [{"order_id": "1001", "product_name": "机械键盘", "message_id": "a1"}],
    )
    assert "不是坏了" in result.question and "不想要" in result.question


async def test_general_colloquial_faq_can_normalize_without_choosing_order():
    result = await resolve("这个产品售后咋办", output("如何申请产品售后服务？", scope="general"))
    assert result.question == "如何申请产品售后服务？" and result.trusted_order_id is None
