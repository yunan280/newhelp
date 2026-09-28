"""工具执行管线 —— 校验 → 超时 → 重试 → 结构化返回。

这一层的存在理由是**工具坏了不该让整轮 500**:任何失败都翻成一个给模型看的
失败说明,由模型据此组织回答。用户看到的是"客服说没查到",不是空白页。

为什么不用 langchain 的 ToolNode / ToolErrorMiddleware:那些在
`langchain.agents` 里,而本章明确不做 Agent Loop —— 引它就等于把多轮循环
一起引进来,正好是本要避免的东西。
"""

import asyncio
import json
import time
from dataclasses import dataclass

from langchain_core.tools import BaseTool
from pydantic import ValidationError

TOOL_TIMEOUT_SECONDS = 3.0

# 1 次初试 + 2 次重试。只对读类工具生效 —— 写类工具由 retryable=False 关掉。
MAX_ATTEMPTS = 3

# 每次尝试之间的退避。长度 = MAX_ATTEMPTS - 1。
BACKOFF_SECONDS = (0.2, 0.4)

# 回灌给模型的工具结果上限。超出会被截断并标注 —— 一条几千字的结果会吃掉
# 整个上下文预算(HISTORY_TOKEN_BUDGET = 2048),把真正的对话挤出去。
# 2000 字符对本章所有工具都绰绰有余(FAQ 单条百来字,工单回执更短)。
TOOL_RESULT_MAX_CHARS = 2000


@dataclass(frozen=True)
class ToolResult:
    """一次工具执行的结果。字段刻意做全,因为它是 eval 与徽章的唯一数据源。

    `ok=False` 时 `content` **同样**回灌给模型 —— 这是"执行错误处理"这条需求的
    落点:工具坏了,模型该知道,并据此告诉用户。
    """

    name: str
    args: dict
    ok: bool
    content: str
    error: str | None
    elapsed_ms: int
    attempts: int


def _truncate(text: str) -> str:
    if len(text) <= TOOL_RESULT_MAX_CHARS:
        return text
    return f"{text[:TOOL_RESULT_MAX_CHARS]}…(已截断,原始返回共 {len(text)} 字符)"


def _as_text(value: object) -> str:
    """ToolMessage.content 必须是 str。

    dict / list 用 JSON 而不是 str():`str({"a": 1})` 给的是 Python repr(单引号),
    模型读起来别扭,嵌套深了还容易看错结构。
    """
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _describe_validation_error(tool_name: str, exc: ValidationError) -> str:
    """把 pydantic 的报错翻成模型能据此改口径的话。

    直接甩 pydantic 原文的话,模型看到的是 `1 validation error for echo` 加一堆
    URL —— 它读得懂,但读不出"我该补哪个参数"。所以把缺的字段名列出来。
    """
    missing = [
        ".".join(str(p) for p in err["loc"])
        for err in exc.errors()
        if err["type"] == "missing"
    ]
    if missing:
        return f"调用 {tool_name} 缺少必要参数:{', '.join(missing)}。请补齐后重试。"
    return f"调用 {tool_name} 的参数不合法:{exc}"


async def execute_tool(
    tool: BaseTool,
    args: dict,
    *,
    retryable: bool,
    timeout: float = TOOL_TIMEOUT_SECONDS,
    sleep=asyncio.sleep,
) -> ToolResult:
    """跑一个工具,任何失败都翻成 ToolResult,不抛。

    `retryable=False` 用于写类工具:没有幂等设施,超时重试会重复建单 ——
    用户投诉一次、工单出来两张。这条与"参数错不重试"是独立的两个判断。

    **超时的诚实边界**:`asyncio.wait_for` 杀不掉已经在线程里跑的那个函数。
    实测 langchain 的 @tool 对同步函数的 ainvoke 已经把它丢进线程池
    (跑在 asyncio_0 线程),所以超时只是让我们不再等它,那个线程会自己跑完。
    本章的工具都是"一次小查询或一次小插入",可以被放弃的代价是有界的 ——
    但这是个真实存在的缝,别当成"超时已经做对了"。

    `sleep` 可注入:测试里不真睡,否则 3 次尝试要睡 0.6 秒,套件又慢又脆。
    """
    started = time.monotonic()

    # 1) 按 args_schema 显式校验参数。校验失败**不重试** —— 参数是模型生成的,
    #    同一个坏参数重试三次只会白烧三倍时间,对"输入错了"这个成因无效。
    if tool.args_schema is not None:
        try:
            validated = tool.args_schema.model_validate(args)
        except ValidationError as exc:
            return ToolResult(
                name=tool.name,
                args=args,
                ok=False,
                content=_describe_validation_error(tool.name, exc),
                error="invalid_args",
                elapsed_ms=int((time.monotonic() - started) * 1000),
                attempts=0,
            )
        # 用校验后的干净参数:多出来的键会被 pydantic 丢掉(默认 extra='ignore')。
        args = validated.model_dump()

    attempts = 0
    last_error: str | None = None
    last_message = ""

    while attempts < (MAX_ATTEMPTS if retryable else 1):
        attempts += 1
        try:
            raw = await asyncio.wait_for(tool.ainvoke(args), timeout=timeout)
        except Exception as exc:  # noqa: BLE001 —— 工具坏了的任何形态都要接住
            last_error = type(exc).__name__
            last_message = f"{type(exc).__name__}: {exc}"
            if attempts < (MAX_ATTEMPTS if retryable else 1):
                await sleep(BACKOFF_SECONDS[attempts - 1])
            continue

        return ToolResult(
            name=tool.name,
            args=args,
            ok=True,
            content=_truncate(_as_text(raw)),
            error=None,
            elapsed_ms=int((time.monotonic() - started) * 1000),
            attempts=attempts,
        )

    if last_error == "TimeoutError":
        content = f"调用 {tool.name} 超时(超过 {timeout} 秒),没能取到结果。"
    else:
        content = f"调用 {tool.name} 失败:{last_message}"

    return ToolResult(
        name=tool.name,
        args=args,
        ok=False,
        content=content,
        error=last_error,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        attempts=attempts,
    )
