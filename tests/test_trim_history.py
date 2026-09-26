"""trim_history 的测试 —— 本章最该被测的纯函数。

token_counter 注入假计数器,让断言完全不依赖 langchain 内部启发式。
"""

from langchain_core.messages import AIMessage, HumanMessage

from mewhelp.memory import trim_history


def char_counter(messages) -> int:
    """1 个字符 = 1 个 token。deterministic,便于断言。"""
    return sum(len(m.content) for m in messages)


def test_empty_history_returns_empty():
    assert trim_history([], max_tokens=100, token_counter=char_counter) == []


def test_history_within_budget_is_returned_unchanged():
    history = [HumanMessage(content="一二三"), AIMessage(content="四五六")]
    result = trim_history(history, max_tokens=100, token_counter=char_counter)
    assert result == history


def test_oldest_messages_dropped_first_when_over_budget():
    history = [
        HumanMessage(content="A" * 10),
        AIMessage(content="B" * 10),
        HumanMessage(content="C" * 10),
        AIMessage(content="D" * 10),
    ]
    result = trim_history(history, max_tokens=25, token_counter=char_counter)
    assert [m.content for m in result] == ["C" * 10, "D" * 10]


def test_trimmed_history_never_starts_with_ai_message():
    """裁完以 AI 消息开头,模型会看到"自己刚说过话"却没有对应提问,容易答非所问。

    不假定某个预算下具体保留几条(那是 trim_messages 的实现细节),
    只钉住真正的不变式:结果非空时,首条必须是 HumanMessage。
    """
    history = [
        HumanMessage(content="A" * 10),
        AIMessage(content="B" * 10),
        HumanMessage(content="C" * 10),
        AIMessage(content="D" * 10),
    ]
    for budget in range(0, 45, 5):
        result = trim_history(history, max_tokens=budget, token_counter=char_counter)
        assert not result or isinstance(result[0], HumanMessage), (
            f"预算 {budget} 裁出了以 AI 消息开头的历史:{result}"
        )


def test_single_message_over_budget_yields_empty_list():
    """一条消息就超预算时返回空,而不是抛异常或硬塞进去。"""
    history = [HumanMessage(content="A" * 100)]
    assert trim_history(history, max_tokens=10, token_counter=char_counter) == []


def test_result_is_a_new_list_not_the_input():
    history = [HumanMessage(content="hi")]
    result = trim_history(history, max_tokens=100, token_counter=char_counter)
    assert result is not history
