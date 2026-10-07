"""工具执行管线 —— 校验 / 超时 / 重试 / 错误回灌。

全部离线:用真的 @tool 装饰本地函数,不碰网络也不碰库。
"""

import json

import pytest
from langchain_core.tools import ToolException, tool

from mewhelp.tools.infra import (
    BACKOFF_SECONDS,
    MAX_ATTEMPTS,
    TOOL_RESULT_MAX_CHARS,
    ToolResult,
    execute_tool,
)


@tool
def echo(text: str) -> str:
    """回显。"""
    return f"echo:{text}"


@tool
def always_fails(reason: str) -> str:
    """总是抛 ValueError。"""
    raise ValueError(f"内部炸了:{reason}")


@tool
def raises_tool_exception(reason: str) -> str:
    """抛 ToolException —— 与普通异常同等处理,都要被接住。"""
    raise ToolException(f"工具级失败:{reason}")


@tool
def flaky(fail_times: int) -> str:
    """前 fail_times 次抛瞬时异常,之后成功。用模块级计数模拟。"""
    _FLAKY_STATE.append(1)
    if len(_FLAKY_STATE) <= fail_times:
        raise TimeoutError("瞬时超时")
    return "好了"


_FLAKY_STATE: list[int] = []


@pytest.fixture(autouse=True)
def _reset_flaky():
    _FLAKY_STATE.clear()
    yield
    _FLAKY_STATE.clear()


async def no_sleep(_seconds: float) -> None:
    """重试的退避在测试里不该真的睡 —— 3 次尝试要睡 0.6 秒,套件会变慢且脆。"""
    return


# ---------- 成功路径 ----------


async def test_successful_call_carries_content_and_metrics():
    result = await execute_tool(echo, {"text": "hi"}, retryable=True, sleep=no_sleep)

    assert isinstance(result, ToolResult)
    assert (result.ok, result.content, result.error) == (True, "echo:hi", None)
    assert result.attempts == 1
    assert result.elapsed_ms >= 0
    assert result.name == "echo"
    assert result.args == {"text": "hi"}


# ---------- 参数校验 ----------


async def test_missing_required_argument_fails_without_calling_the_tool():
    result = await execute_tool(echo, {}, retryable=True, sleep=no_sleep)

    assert result.ok is False
    assert result.error == "invalid_args"
    assert result.attempts == 0  # 一次都没调
    assert "text" in result.content  # 报错里要说清缺的是哪个参数


async def test_invalid_argument_types_are_caught_before_running():
    result = await execute_tool(echo, {"text": {"nested": 1}}, retryable=True, sleep=no_sleep)
    assert result.ok is False
    assert result.error == "invalid_args"


async def test_invalid_args_are_never_retried():
    """Review Focus #4 的对照组:参数错不重试。

    参数是模型生成的,同一个坏参数重试三次只会白烧三倍时间 ——
    重试对"输入错了"这个成因无效。校验失败应当立刻回灌,让模型自己改口径。
    """
    result = await execute_tool(echo, {}, retryable=True, sleep=no_sleep)
    assert result.attempts == 0
    assert result.elapsed_ms < 100  # 没有真的试三次


async def test_extra_argument_keys_are_rejected():
    """额外参数不静默丢弃，严格校验后回灌错误。"""
    result = await execute_tool(
        echo, {"text": "hi", "unexpected": 1}, retryable=True, sleep=no_sleep
    )
    assert result.ok is False
    assert result.status == '校验拦下'
    assert result.attempts == 0
    assert 'unexpected' in result.content


# ---------- 重试 ----------


async def test_transient_failure_is_retried_then_succeeds():
    result = await execute_tool(flaky, {"fail_times": 1}, retryable=True, sleep=no_sleep)

    assert result.ok is True
    assert result.content == "好了"
    assert result.attempts == 2


async def test_backoff_schedule_is_followed():
    """退避序列要真的被用上 —— 否则重试会变成对上游的三连击。"""
    slept: list[float] = []

    async def spy_sleep(seconds: float) -> None:
        slept.append(seconds)

    await execute_tool(flaky, {"fail_times": 2}, retryable=True, sleep=spy_sleep)

    assert slept == list(BACKOFF_SECONDS)


async def test_gives_up_after_max_attempts_and_reports_the_count():
    result = await execute_tool(flaky, {"fail_times": 99}, retryable=True, sleep=no_sleep)

    assert result.ok is False
    assert result.attempts == MAX_ATTEMPTS
    assert result.error == "TimeoutError"


async def test_write_tools_are_never_retried():
    """写类工具一律不重试 —— 没有幂等设施,超时重试会**重复建单**。

    这条与"参数错不重试"是独立的:即使成因是瞬时异常也不重试。
    用户投诉一次、工单出来两张,是这个洞的形态。
    """
    result = await execute_tool(flaky, {"fail_times": 99}, retryable=False, sleep=no_sleep)

    assert result.ok is False
    assert result.attempts == 1


# ---------- 错误回灌 ----------


@pytest.mark.parametrize(
    ("tool_obj", "expected_error"),
    [
        pytest.param(always_fails, "ValueError", id="普通异常"),
        pytest.param(raises_tool_exception, "ToolException", id="ToolException"),
    ],
)
async def test_tool_exceptions_never_bubble_up(tool_obj, expected_error):
    """工具坏了不能让整轮 500 —— 抛出去的话这次请求就结束了,用户什么也看不到。

    实测:ValueError 与 ToolException 都会从 ainvoke 原样抛出(默认
    handle_tool_error 是关的),所以管线必须自己接。
    """
    result = await execute_tool(tool_obj, {"reason": "x"}, retryable=True, sleep=no_sleep)

    assert result.ok is False
    assert result.error == expected_error
    assert "x" in result.content  # 失败说明里带着原因,模型据此告诉用户


async def test_error_content_is_not_empty_so_the_model_can_tell_what_happened():
    """失败时 content 必须有内容。

    空串回灌给模型,模型分不清"查了没有"和"工具坏了",容易编答案。
    """
    result = await execute_tool(always_fails, {"reason": "boom"}, retryable=True, sleep=no_sleep)
    assert result.content.strip()
    assert "boom" in result.content


# ---------- 超时 ----------


async def test_timeout_is_reported_as_such():
    @tool
    def slow(seconds: float) -> str:
        """睡一会儿。"""
        import time

        time.sleep(seconds)
        return "醒了"

    result = await execute_tool(slow, {"seconds": 1.0}, retryable=False, timeout=0.05)
    assert result.ok is False
    assert result.error == "TimeoutError"
    assert "超时" in result.content


# ---------- 返回值形状 ----------


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"a": 1}, id="dict"),
        pytest.param([1, 2], id="list"),
    ],
)
async def test_non_string_tool_output_is_serialized_for_the_model(payload):
    """工具返回非字符串时要转成文本 —— ToolMessage.content 必须是 str。

    直接 str() 一个 dict 会得到 Python repr(单引号),模型看着别扭但能用;
    这里用 JSON,是为了让嵌套结构保持可读。
    """

    @tool
    def returns_json() -> dict | list:
        """返回结构化数据。"""
        return payload

    result = await execute_tool(returns_json, {}, retryable=True, sleep=no_sleep)
    assert result.ok is True
    assert json.loads(result.content) == payload


async def test_oversized_tool_output_is_truncated():
    """Review Focus #3:工具返回超长内容时回灌前要截断。

    现在 mock 工具与 FAQ 都短,但 create_ticket 回灌的是工单原文。
    一条几千字的结果灌回模型会吃掉整个上下文预算(HISTORY_TOKEN_BUDGET = 2048),
    把真正的对话挤出去 —— 而症状是"模型答得莫名其妙",不是报错。
    """

    @tool
    def verbose() -> str:
        """返回一大段。"""
        return "字" * (TOOL_RESULT_MAX_CHARS * 3)

    result = await execute_tool(verbose, {}, retryable=True, sleep=no_sleep)

    assert result.ok is True
    assert len(result.content) < TOOL_RESULT_MAX_CHARS * 2
    assert result.content.startswith("字")
    # 截断必须说出来,否则模型以为这就是全部内容
    assert "截断" in result.content


async def test_knowledge_artifact_keeps_full_content_and_source_snapshots():
    from langchain_core.tools import tool

    from mewhelp.knowledge.retrieval import RankedChunk, RetrievalResult
    from mewhelp.knowledge.store import ChunkSnapshot
    from mewhelp.tools.infra import execute_tool

    body = "完整证据" * 900
    chunk = ChunkSnapshot(
        9007199254740993, body, "问题", body, "章节", "参数", None, "manual", False, "a" * 64
    )
    evidence = RetrievalResult([chunk], [RankedChunk(chunk, 0.9)])

    @tool(response_format="content_and_artifact")
    async def query_faq(keyword: str):
        """Retrieve complete knowledge evidence."""
        return body, evidence

    result = await execute_tool(query_faq, {"keyword": "问题"}, retryable=False)
    assert result.ok and result.content == body and result.artifact is evidence
    assert result.artifact.final[0].chunk.id == 9007199254740993
