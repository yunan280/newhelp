"""注册表 —— 按名查、批量跑、未知工具不抛。"""

import pytest
from langchain_core.tools import tool

from mewhelp.tools.infra import ToolResult
from mewhelp.tools.registry import ToolRegistry, ToolSpec


async def test_faq_retrieval_can_outlast_generic_three_second_tool_deadline(monkeypatch):
    """BGE-M3 首次载入会超过旧工具 3 秒时限；FAQ 应返回答案而非超时。"""
    import time

    from mewhelp.tools import ticket

    @tool
    def query_faq(keyword: str) -> str:
        """模拟首次载入嵌入模型后按用户问法检索。"""
        time.sleep(3.2)
        return "满 99 元包邮。"

    monkeypatch.setattr(ticket, "build_knowledge_tools", lambda _factory: [query_faq])
    registry = ticket.build_registry(lambda: None, conversation_id=1)
    result = await registry.run("query_faq", {"keyword": "邮费是多少"})
    assert result.ok is True
    assert result.content == "满 99 元包邮。"
    assert result.attempts == 1


@tool
def ok_tool(text: str) -> str:
    """成功。"""
    return f"ok:{text}"


@tool
def bad_tool(text: str) -> str:
    """失败。"""
    raise ValueError("坏了")


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry({
        "ok_tool": ToolSpec(tool=ok_tool),
        "bad_tool": ToolSpec(tool=bad_tool, retryable=False),
    })


def test_names_and_tools(registry):
    assert registry.names() == ["ok_tool", "bad_tool"]
    assert [t.name for t in registry.tools()] == ["ok_tool", "bad_tool"]


def test_get_returns_none_for_an_unknown_name(registry):
    assert registry.get("nope") is None


def test_tools_are_what_bind_tools_needs(registry):
    """`bind_tools` 要的是 BaseTool 列表 —— 形状断在测试里,
    免得将来有人好心改成返回 ToolSpec 列表,一路绿到真机才炸。"""
    from langchain_core.utils.function_calling import convert_to_openai_tool

    for t in registry.tools():
        assert "function" in convert_to_openai_tool(t)


async def test_run_delegates_and_returns_a_tool_result(registry):
    result = await registry.run("ok_tool", {"text": "hi"})
    assert isinstance(result, ToolResult)
    assert result.content == "ok:hi"


async def test_run_on_an_unknown_tool_returns_a_structured_failure(registry):
    """模型编了个不存在的工具名 —— 这不是异常,是要回灌给模型的一条结果。

    抛出去的话整轮就断了,而模型本来只要被告知"没这个工具"就能自己改。
    """
    result = await registry.run("query_stock", {"sku": "1"})

    assert result.ok is False
    assert result.error == "unknown_tool"
    assert "query_stock" in result.content
    assert result.attempts == 0
    # 回灌的内容里要带上现有的工具名,模型据此改口
    assert "ok_tool" in result.content


async def test_run_respects_the_specs_retryable_flag(registry):
    result = await registry.run("bad_tool", {"text": "x"})
    assert result.attempts == 1  # 没重试


async def test_run_all_executes_every_call_and_keeps_the_order(registry):
    """一轮里模型可能同时发多个 tool_calls —— **全都要执行**(spec §11)。

    不因为"只允许调一次"就丢掉第二个:那等于模型说的话被静默截断了,
    而用户看不到任何痕迹。断言顺序是为了让 args 与结果一一对应。
    """
    calls = [
        {"name": "ok_tool", "args": {"text": "a"}, "id": "c1"},
        {"name": "ok_tool", "args": {"text": "b"}, "id": "c2"},
    ]
    results = await registry.run_all(calls)

    assert [r.content for r in results] == ["ok:a", "ok:b"]


async def test_run_all_mixes_successes_and_failures(registry):
    calls = [
        {"name": "ok_tool", "args": {"text": "a"}, "id": "c1"},
        {"name": "bad_tool", "args": {"text": "b"}, "id": "c2"},
    ]
    results = await registry.run_all(calls)
    assert [r.ok for r in results] == [True, False]


async def test_run_all_on_an_empty_list_returns_empty(registry):
    assert await registry.run_all([]) == []


async def test_run_all_accepts_tool_calls_objects_not_just_dicts(registry):
    """`AIMessage.tool_calls` 里是 dict,但 langchain 也允许对象形态
    (ToolCall 是 TypedDict,可被当作属性访问)。这里只认 dict 的
    `["name"]`/`["args"]` —— 断一下,免得将来换了取法。
    """
    from langchain_core.messages import AIMessage

    ai = AIMessage(content="", tool_calls=[
        {"name": "ok_tool", "args": {"text": "a"}, "id": "c1", "type": "tool_call"},
    ])
    results = await registry.run_all(ai.tool_calls)
    assert results[0].content == "ok:a"
