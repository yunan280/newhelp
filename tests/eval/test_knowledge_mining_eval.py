"""真实模型评估：Prompt 与抽取结果属于数据产物，以标注样例验证。"""

import pytest

from mewhelp.knowledge.mining import ConversationSample, llm_extract


@pytest.mark.eval
def test_extracts_reusable_shipping_qa_without_private_case():
    samples = [
        ConversationSample(1, "user: 邮费是多少？\nassistant: 单笔满 99 元包邮，不满 99 元按收货地收取 8 元起。"),
        ConversationSample(2, "user: 我的订单 123456789012 什么时候到？\nassistant: 你的订单今天送到上海市某路 1 号。"),
    ]
    items = llm_extract(samples)
    assert any(item.source_conversation_id == 1 and "99" in item.answer for item in items)
    assert all(item.source_conversation_id != 2 for item in items)
