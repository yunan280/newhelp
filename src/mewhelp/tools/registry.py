"""工具注册表 —— 按名查、批量跑。

注册表持有的是 `ToolSpec` 而不是裸 `BaseTool`,因为"要不要重试"是**工具级**
的策略(写类工具不重试),不是执行时的临时判断。
"""

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from langchain_core.tools import BaseTool

from .infra import TOOL_TIMEOUT_SECONDS, ToolResult, execute_tool


@dataclass(frozen=True)
class ToolSpec:
    tool: BaseTool
    # 写类工具设 False。没有幂等设施时重试会重复建单。
    retryable: bool = True
    timeout_seconds: float = TOOL_TIMEOUT_SECONDS
    preserve_raw: bool = False


class ToolRegistry:
    def __init__(self, specs: Mapping[str, ToolSpec]) -> None:
        self._specs = dict(specs)

    def names(self) -> list[str]:
        return list(self._specs)

    def tools(self) -> list[BaseTool]:
        """给 `bind_tools` 用的列表。"""
        return [spec.tool for spec in self._specs.values()]

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    async def run(self, name: str, args: dict) -> ToolResult:
        """跑单个工具。未知工具名是一条**结构化失败**,不是异常。

        模型编了个不存在的工具名是常见事,它只要被告知"没这个工具"就能自己改口;
        抛出去的话整轮就断了,用户什么都看不到。
        """
        spec = self._specs.get(name)
        if spec is None:
            return ToolResult(
                name=name,
                args=args,
                ok=False,
                content=(
                    f"没有名为 {name} 的工具。可用的工具有:{', '.join(self.names())}。"
                    "请改用其中之一,或直接回答用户。"
                ),
                error="unknown_tool",
                elapsed_ms=0,
                attempts=0,
            )
        return await execute_tool(
            spec.tool, args, retryable=spec.retryable, timeout=spec.timeout_seconds,
            preserve_raw=spec.preserve_raw,
        )

    async def run_all(self, calls: Sequence[Mapping]) -> list[ToolResult]:
        """一轮里的所有 tool_calls **全部执行**,并发跑,结果顺序与入参一致。

        不因为"只允许调一次"就丢掉第二个 —— 那等于模型说的话被静默截断了。
        也不做"执行完第一个发现够了就跳过其余"—— 那需要一个判断"够了"的规则,
        而那个规则本身就是 Agent Loop 的雏形。
        """
        if not calls:
            return []
        return list(
            await asyncio.gather(
                *(self.run(call["name"], dict(call["args"])) for call in calls)
            )
        )
