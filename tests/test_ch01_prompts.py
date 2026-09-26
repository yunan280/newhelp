"""Prompt 模板结构的测试。行为约束的验证在 tests/eval/,不在这里。"""

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from mewhelp.ch01.prompts import (
    CHAT_PROMPT,
    EXTRACT_PROMPT,
    EXTRACT_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
)
from mewhelp.ch01.schemas import AfterSalesTicket


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


def test_extract_system_prompt_defines_the_explanation_boundary():
    """「仅需解释」的判据原文必须钉住 —— 27 条标注的 key 是从这段规则推出来的。

    与上面退款/退货那条同理:钉判据原文,不钉关键词。措辞一改,标注集的基准就
    悄悄失效,而失效会以"模型变差了"的形式出现(修复轮 1 实测过一次:两条散文
    通道对问句给出相反答案,模型的预测跟着翻)。要改这句话,测试必须同步改。
    """
    assert "- expected_solution:用户**希望怎么解决**。用户要的是**信息**" in EXTRACT_SYSTEM_PROMPT
    assert (
        "而不是要我们做退款/换货/维修/补发/补偿这类动作时,填「仅需解释」" in EXTRACT_SYSTEM_PROMPT
    )
    assert "才填「未提及」。" in EXTRACT_SYSTEM_PROMPT


def test_explanation_boundary_is_worded_identically_in_both_prose_channels():
    """两条散文通道必须说同一条规则(修复轮 2 裁定 2)。

    修复轮 1 的实际状态:prompts.py 写「没提要求→未提及」,Field description 写
    「只在问→仅需解释」,对问句给出**相反**答案 —— 实测模型跟的是 Field description。
    两条通道互相打架本身就是缺陷,不管哪条赢,所以这里钉"两边共有同一段判据原文"。
    """
    rule_chunk = "而不是要我们做退款/换货/维修/补发/补偿这类动作时,填「仅需解释」"
    description = AfterSalesTicket.model_fields["expected_solution"].description
    assert rule_chunk in EXTRACT_SYSTEM_PROMPT
    assert rule_chunk in description, "Field description 跑偏了,两条通道又开始打架"
    assert "才填「未提及」。" in description


def test_no_secret_looking_strings_in_prompts():
    for text in (SYSTEM_PROMPT, EXTRACT_SYSTEM_PROMPT):
        assert "sk-" not in text
