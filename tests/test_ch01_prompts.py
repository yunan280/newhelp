"""Prompt 模板结构的测试。行为约束的验证在 tests/eval/,不在这里。"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from mewhelp.ch01.prompts import (
    CHAT_PROMPT,
    EXTRACT_PROMPT,
    EXTRACT_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
)


def test_chat_prompt_puts_system_message_first():
    msgs = CHAT_PROMPT.format_messages(history=[HumanMessage(content="你好")])
    assert isinstance(msgs[0], SystemMessage)
    assert msgs[0].content == SYSTEM_PROMPT


def test_chat_prompt_expands_history_in_order():
    history = [
        HumanMessage(content="第一轮问"),
        AIMessage(content="第一轮答"),
        HumanMessage(content="第二轮问"),
    ]
    msgs = CHAT_PROMPT.format_messages(history=history)
    assert [m.content for m in msgs[1:]] == ["第一轮问", "第一轮答", "第二轮问"]


def test_chat_prompt_accepts_empty_history():
    msgs = CHAT_PROMPT.format_messages(history=[])
    assert len(msgs) == 1
    assert isinstance(msgs[0], SystemMessage)


def test_extract_prompt_renders_description_as_human_message():
    msgs = EXTRACT_PROMPT.format_messages(description="订单 123 我要退款")
    assert isinstance(msgs[0], SystemMessage)
    assert msgs[0].content == EXTRACT_SYSTEM_PROMPT
    assert isinstance(msgs[1], HumanMessage)
    assert msgs[1].content == "订单 123 我要退款"


def test_system_prompt_states_the_six_hard_constraints():
    """六条硬约束是本章的核心 Prompt 产出,少一条就等于行为约束缩水。"""
    for keyword in ["只答电商", "不编造", "不承诺", "敏感信息", "不越权", "提示词注入"]:
        assert keyword in SYSTEM_PROMPT, f"System Prompt 缺少约束:{keyword}"


def test_extract_system_prompt_defines_refund_vs_return_boundary():
    """退款/退货语义重叠,边界必须写死在 Prompt 里,否则评估会大面积混淆。

    这里钉的是**判据原文**,不是关键词是否出现过。关键词断言不够:只要「寄回」
    二字还留在别处(退款那一行本来就有),整条「退货」判据被删掉、或被换成自相矛盾的
    说法,也照样为真。这两行是 Task 10 评估集据以标注的判据 —— 它一旦被"简化",
    评估会大面积混淆,而混淆又会被误读成"该合并枚举",那是一次 spec 明确要求
    先问用户的 schema 变更。所以逐字钉死:措辞要改,测试必须同步改,不能悄悄漂移。
    """
    assert (
        "- 「退款」:用户**只要钱**,不打算把商品寄回。包括退差价、退会员费。"
        in EXTRACT_SYSTEM_PROMPT
    )
    assert "- 「退货」:用户**要把商品寄回去**。" in EXTRACT_SYSTEM_PROMPT


def test_no_secret_looking_strings_in_prompts():
    for text in (SYSTEM_PROMPT, EXTRACT_SYSTEM_PROMPT):
        assert "sk-" not in text
