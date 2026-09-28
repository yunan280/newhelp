"""AGENT_SYSTEM 的结构要求。

prompt 的**质量**不可单测 —— 那由评估集验(Task 17)。但它的**结构**可以:
有几句话必须存在,缺了的话某条验收会静默失效,而且不会有任何报错。
这些测试守的就是那几句。
"""

from mewhelp.ch02.prompts import AGENT_SYSTEM


def test_forbids_answering_policy_questions_from_parametric_knowledge():
    """验收③的配套机关(spec §8)。

    不禁的话,模型可能用自己的知识直接答出邮费 —— 于是验收③观察到的是
    "模型没调工具",而不是"查表查不出来"。那是另一件事,不能混为一谈。
    """
    assert "query_faq" in AGENT_SYSTEM
    assert any(
        phrase in AGENT_SYSTEM
        for phrase in ("不许用自己的知识", "不要凭你自己的知识", "不得凭你自己的知识")
    )


def test_tells_the_model_to_be_honest_when_the_lookup_misses():
    """工具说没查到,模型必须如实转告,不能补一个像样的答案。"""
    assert "没查到" in AGENT_SYSTEM or "没有查到" in AGENT_SYSTEM


def test_keeps_the_customer_service_persona():
    """工具链长在客服聊天里,不是长在一个通用 Agent 上 —— 人设不能丢。"""
    assert "MewHelp" in AGENT_SYSTEM
    assert "客服" in AGENT_SYSTEM


def test_keeps_ch01s_hard_constraints():
    """ch01 那六条硬约束里与本章相关的部分要继续在:不编造、不承诺、拒绝注入。"""
    for topic in ("不编造", "不承诺", "注入"):
        assert topic in AGENT_SYSTEM


def test_mentions_all_five_tools_by_name():
    """模型靠名字选工具;prompt 里点出它们是必要的,虽然 schema 里也有。"""
    for name in ("query_order", "query_product", "query_logistics", "query_faq", "create_ticket"):
        assert name in AGENT_SYSTEM


def test_does_not_instruct_a_multi_turn_loop():
    """本章是**单轮**:prompt 里不许出现"再查一次""多轮确认"这类指令。

    收敛是靠结构(不 bind_tools)保证的,不是靠 prompt —— 但 prompt 反向误导
    会让模型在第一轮就不肯调工具。
    """
    assert "再调用" not in AGENT_SYSTEM
    assert "反复" not in AGENT_SYSTEM
