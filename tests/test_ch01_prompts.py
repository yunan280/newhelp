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


_EXPECTED_SOLUTION_BULLET = "- expected_solution:"


def _expected_solution_rule_in_prompt() -> str:
    """切出 EXTRACT_SYSTEM_PROMPT 里 expected_solution 那一条规则的**正文**。

    正文 = 从 `- expected_solution:` 起、连同它后面所有缩进的续行,直到空行或下一个
    顶格行为止。切出来的是这一条规则的**整段**,不是其中一个共享片段 —— 这正是与旧
    断言的关键区别(旧断言只问"那段话还在不在",在一侧末尾追加一段直接矛盾的字照样绿)。
    """
    lines = EXTRACT_SYSTEM_PROMPT.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(_EXPECTED_SOLUTION_BULLET))
    block = [lines[start][len(_EXPECTED_SOLUTION_BULLET):]]
    for line in lines[start + 1:]:
        if not line.strip() or not line[:1].isspace():
            break
        block.append(line)
    return "\n".join(block)


def _rule_fingerprint(text: str) -> str:
    """判据比对前的归一化:**只抹平排版,一个字都不动。**

    Prompt 里那段是缩进的 bullet,「希望怎么解决」还带着 `**`;Field description 是
    拼接好的单串、没有那对 `**`。说的到底是不是同一件事,不该被这两处排版差异挡住 ——
    但也**只**归一化这两样:任何多出来的字(比如顺手追加的一句话)都会让比较不等,
    这正是它比"共享子串还在"强的地方。
    """
    return "".join(text.replace("**", "").split())


def test_explanation_boundary_is_worded_identically_in_both_prose_channels():
    """两条散文通道必须**整段**说同一条规则(修复轮 2 裁定 2;终评 Important-2 加固)。

    修复轮 1 的实际状态:prompts.py 写「没提要求→未提及」,Field description 写
    「只在问→仅需解释」,对问句给出**相反**答案 —— 实测模型跟的是 Field description。
    两条通道互相打架本身就是缺陷,不管哪条赢。

    旧版本的钉子只钉"两处共有同一段子串"。终评拿三个变异量过它:**在一侧末尾追加一段
    直接矛盾的话、共享片段原样保留 ⇒ 99 passed,一次都没红**;而真模型探针显示矛盾一加,
    `#18` 立刻翻成「未提及」。这段散文是 27 条标注的基准,锚点漂了会表现成"模型变差了",
    所以钉子升级成:**两边这一段判据归一化后必须逐字相等**。
    """
    description = AfterSalesTicket.model_fields["expected_solution"].description
    prompt_rule = _expected_solution_rule_in_prompt()
    prompt_fp = _rule_fingerprint(prompt_rule)
    description_fp = _rule_fingerprint(description)

    # 先挡住"切空了 / 被删瘦了"这类退化解 —— 空串和空串当然相等。
    assert len(prompt_fp) > 80, (
        f"expected_solution 判据段只剩 {len(prompt_fp)} 个字,像是被删了:{prompt_rule!r}"
    )

    assert prompt_fp == description_fp, (
        "两条散文通道对 expected_solution 的说法不再相同:\n"
        f"  prompts.py : {prompt_rule!r}\n"
        f"  schemas.py : {description!r}"
    )

    # 上面的等式只管"这一段"。一段矛盾的话完全可以**另起一行**追加在别处,共享的那段
    # 原样不动 —— 那样等式照样成立,而 Prompt 已经自相矛盾。判据里只有这两个取值名,
    # 任何关于本字段的新规则几乎必然点到其中一个,所以要求它们**只出现在这一段里**,
    # 把"另起一行"那条路一起堵上。(不点这两个名字的改写仍可能溜过,这是本守卫的已知边界。)
    outside_the_rule = EXTRACT_SYSTEM_PROMPT.replace(prompt_rule, "")
    for token in ("仅需解释", "未提及"):
        assert token not in outside_the_rule, (
            f"「{token}」出现在 expected_solution 判据段之外 —— 两条通道又在各说各话"
        )


def test_no_secret_looking_strings_in_prompts():
    for text in (SYSTEM_PROMPT, EXTRACT_SYSTEM_PROMPT):
        assert "sk-" not in text
