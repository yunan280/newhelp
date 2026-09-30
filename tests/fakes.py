"""测试替身 —— 多个测试文件共用。

为什么不用 langchain 的 GenericFakeChatModel:实测它的 `bind_tools` 直接抛
NotImplementedError,而 ch02 的编排核心全程都要 bind_tools。ch01 那套假模型
在这里用不了,得自己写一个。
"""

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field


def tool_call_chunks(
    name: str, args_json: str, call_id: str = "call_1", index: int = 0
) -> list[AIMessageChunk]:
    """把一次工具调用拆成两个块 —— 模拟真实上游的分片下发。

    真实上游的 args 是**分片**吐出来的,`tool_call_chunks` 里先来 name、再来半截
    JSON。所以测试必须走分片这条路,不然"边流边攒 tool_calls"这个能力
    在测试里根本没被验过。

    `index` **不是**可有可无的装饰:一轮里有多个 tool_calls 时,合并是按 index 归并的
    (实测 —— 两个调用都用 index=0 时,第二个调用的 args 会变成 `{}`,即被当成同一个
    调用续写)。所以同一轮的第 N 个调用必须传 index=N,否则测试里的"第二个调用"
    是个空参数的空壳,而它恰好会以 ok=False 的样子蒙混过关。
    """
    half = len(args_json) // 2
    return [
        AIMessageChunk(content="", tool_call_chunks=[
            {"name": name, "args": args_json[:half], "id": call_id, "index": index}
        ]),
        AIMessageChunk(content="", tool_call_chunks=[
            {"args": args_json[half:], "index": index}
        ]),
    ]


def text_chunks(*pieces: str) -> list[AIMessageChunk]:
    return [AIMessageChunk(content=p) for p in pieces]


class FakeToolChatModel(BaseChatModel):
    """按剧本吐块的假模型。`bind_tools` 返回自己,并记下绑了什么。

    `rounds` 里每一项是"一次模型调用"要吐的块列表,按调用顺序消费。
    """

    rounds: list[list[AIMessageChunk]] = Field(default_factory=list)
    bound_tools: list = Field(default_factory=list)
    bind_calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "fake-tool-chat-model"

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = list(tools)
        self.bind_calls += 1
        return self

    def _next_round(self) -> list[AIMessageChunk]:
        if not self.rounds:
            return [AIMessageChunk(content="")]
        return self.rounds.pop(0)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        chunks = self._next_round()
        message = chunks[0]
        for chunk in chunks[1:]:
            message = message + chunk
        return ChatResult(generations=[ChatGeneration(message=AIMessage(message.content,
                                                                        tool_calls=message.tool_calls))])

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        for chunk in self._next_round():
            yield ChatGenerationChunk(message=chunk)


def patch_query_understanding(monkeypatch):
    """Isolate the existing business/stream/locking tests from the real classifier.

    Synthetic labels such as A/B exercise locking, not intent classification.
    Classification itself has separate labeled live validation and Ch04 tests.
    """
    from mewhelp.ch02 import service
    from mewhelp.knowledge.query import QueryUnderstanding

    async def understand(question, **kwargs):
        route = "business" if any(word in question for word in ("订单", "物流", "库存", "有货", "价格", "工单")) else "greeting"
        if "退货政策" in question:
            route = "knowledge"
        return QueryUnderstanding(question, question, question, route, [])

    monkeypatch.setattr(service, "understand_query", understand)
