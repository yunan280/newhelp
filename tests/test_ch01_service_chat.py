"""stream_chat 的测试 —— 用假模型,不联网。

覆盖两条 Review Focus:历史被裁空仍能回答、同会话并发不交错。
"""

import asyncio

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from mewhelp.ch01 import service
from mewhelp.memory import SessionStore


def patch_model(monkeypatch, *replies: str):
    """把 service 里取的模型换成按序返回的假模型。"""
    fake = GenericFakeChatModel(messages=iter([AIMessage(content=r) for r in replies]))
    monkeypatch.setattr(service, "get_chat_model", lambda **kw: fake)
    return fake


class SpyPrompt:
    """包住真的 CHAT_PROMPT,把每次渲染出的消息记下来。

    直接 patch 模板实例的 format_messages 不行 —— ChatPromptTemplate 是
    pydantic 模型,不允许随便挂新属性。所以整个替换掉。
    """

    def __init__(self, real, sink: list):
        self.real = real
        self.sink = sink

    def format_messages(self, **kwargs):
        messages = self.real.format_messages(**kwargs)
        self.sink.append(messages)
        return messages


def patch_prompt(monkeypatch, sink: list):
    monkeypatch.setattr(service, "CHAT_PROMPT", SpyPrompt(service.CHAT_PROMPT, sink))


async def collect(session_id: str, message: str) -> tuple[str, list]:
    chunks = [c async for c in service.stream_chat(session_id, message)]
    history = await service.store.get(session_id)
    return "".join(chunks), history


async def test_yields_the_full_reply_text(monkeypatch):
    service.store = SessionStore()
    patch_model(monkeypatch, "您好,一般 48 小时内发货。")
    text, _ = await collect("s1", "几点发货?")
    assert text == "您好,一般 48 小时内发货。"


async def test_reply_and_question_are_written_back_to_history(monkeypatch):
    service.store = SessionStore()
    patch_model(monkeypatch, "在的")
    _, history = await collect("s1", "在吗")
    assert [type(m) for m in history] == [HumanMessage, AIMessage]
    assert history[0].content == "在吗"
    assert history[1].content == "在的"


async def test_second_turn_sees_first_turn_in_the_prompt(monkeypatch):
    """验收标准②的核心:第二轮必须能拿到第一轮的历史。"""
    service.store = SessionStore()
    patch_model(monkeypatch, "第一轮的回答", "第二轮的回答")
    seen: list[list] = []
    patch_prompt(monkeypatch, seen)

    await collect("s1", "我上周买的跑鞋还没发货")
    await collect("s1", "那我还要等多久?")

    second_prompt = [m.content for m in seen[1]]
    assert "我上周买的跑鞋还没发货" in second_prompt
    assert "第一轮的回答" in second_prompt


async def test_sessions_do_not_leak_into_each_other(monkeypatch):
    service.store = SessionStore()
    patch_model(monkeypatch, "给 s1 的", "给 s2 的")
    seen: list[list] = []
    patch_prompt(monkeypatch, seen)

    await collect("s1", "会话一的问题")
    await collect("s2", "会话二的问题")

    assert "会话一的问题" not in [m.content for m in seen[1]]


async def test_works_when_history_is_fully_trimmed(monkeypatch):
    """Review Focus #3:预算小到把历史全裁光,只剩 system + 本轮提问,仍须正常回答。"""
    service.store = SessionStore()
    patch_model(monkeypatch, "第一轮", "第二轮")
    monkeypatch.setattr(service, "HISTORY_TOKEN_BUDGET", 1)

    await collect("s1", "很长很长很长很长很长很长的问题")
    text, history = await collect("s1", "又一个很长很长很长很长很长的问题")

    assert text == "第二轮"
    # 历史本身照常累积(裁剪只影响发给模型的副本,不删存储)
    assert len(history) == 4


async def test_concurrent_turns_on_same_session_are_serialised(monkeypatch):
    """Review Focus #5:同会话并发必须一前一后,两轮都读不到对方未写完的历史。

    断言的**主力**是 seen 的 prompt 长度,不是最终历史 —— 理由见下。
    """
    service.store = SessionStore()
    patch_model(monkeypatch, "A 的回答", "B 的回答")
    seen: list[list] = []
    patch_prompt(monkeypatch, seen)

    async def turn(msg: str):
        return [c async for c in service.stream_chat("s1", msg)]

    await asyncio.gather(turn("问题一"), turn("问题二"))

    history = await service.store.get("s1")
    # 两轮各写回 1 human + 1 ai = 4 条,一条都不能丢
    assert len(history) == 4
    # 不假定谁先拿到锁,但必须严格 human/ai 交替 —— 出现连续两条 human
    # 就说明两轮交错写进来了
    assert [type(m) for m in history] == [
        HumanMessage,
        AIMessage,
        HumanMessage,
        AIMessage,
    ]
    assert {history[0].content, history[2].content} == {"问题一", "问题二"}

    # ---- 以下才是这条测试真正的守门断言 ----
    # 上面那三条**区分不了有锁和无锁**:append(sid, human, reply) 一次原子写入两条,
    # 无论怎么交错,长度都还是 4、类型都还是交替、第 0/2 位也还是两条提问。
    # 唯一能区分的是"每轮开始时读到的历史长度":
    #   持锁 → 先跑的那轮读到 0 条,后跑的那轮读到 2 条 → prompt 长度 {1, 3}
    #   无锁 → 两轮都读到 0 条                                 → prompt 长度 {1, 1}
    # CHAT_PROMPT = [system] + MessagesPlaceholder("history"),所以 prompt 长度 = 1 + 历史条数。
    # 不假定哪一轮先跑,故断言多重集而非顺序。
    assert sorted(len(p) for p in seen) == [1, 3], (
        f"两轮拿到的 prompt 长度是 {sorted(len(p) for p in seen)},期望 [1, 3]。"
        "若是 [1, 1],说明两轮读到了同一份空历史 —— 锁没盖住整轮读-改-写。"
    )


async def test_model_exception_propagates_to_caller(monkeypatch):
    """service 不吞异常 —— 由 api 层负责转成 SSE error 事件。"""

    class Boom:
        async def astream(self, messages):
            raise RuntimeError("上游断了")
            # 不可达,只为让本方法成为**异步生成器**而非协程函数:
            # 协程函数下 `async for` 会先抛 TypeError,断言就测不到上游异常了
            yield

    service.store = SessionStore()
    monkeypatch.setattr(service, "get_chat_model", lambda **kw: Boom())

    with pytest.raises(RuntimeError, match="上游断了"):
        await collect("s1", "在吗")

    # 失败的一轮不写回历史
    assert await service.store.get("s1") == []
