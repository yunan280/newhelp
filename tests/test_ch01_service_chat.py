"""stream_chat 的测试 —— 用假模型,不联网。

覆盖两条 Review Focus:历史被裁空仍能回答、同会话并发不交错。
"""

import asyncio

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

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


# 本文件所有用例一律把历史预算钉在这个固定值上,不吃 `mewhelp.config` 的现值。
# 这不是洁癖,是为了堵两类坑:
#
# 1. **静默失活**(最要命的):预算小到把历史裁空时,凡是"某内容出现在 prompt 里 /
#    没出现在 prompt 里"的断言都会失去判别力。实测 `test_sessions_do_not_leak_into_each_other`
#    在预算 ≤ 8 时,即使把三个 store 调用全钉成同一个 session_id(彻底的隔离破坏),
#    它照样绿 —— 泄漏的证据在拼 prompt 前就被裁掉了:判据还在,只是永远不成立,
#    测试照样报 PASS。这是最坏的一种坏。
# 2. **归错因**:依赖"历史进了 prompt"的用例会因此变红,而失败信息容易被读成
#    "锁没生效",把人带偏。
#
# 用 autouse 而不是逐用例 monkeypatch:新增用例不会再继承一个被改过的预算。
# 需要故意把预算压小的用例(如 `test_works_when_history_is_fully_trimmed`)
# 在自己的函数体里再 setattr 一次即可 —— 后设的覆盖前者。
PINNED_TOKEN_BUDGET = 4096


@pytest.fixture(autouse=True)
def _pin_history_budget(monkeypatch):
    monkeypatch.setattr(service, "HISTORY_TOKEN_BUDGET", PINNED_TOKEN_BUDGET)


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
    # 预算由文件级 autouse fixture 钉在 PINNED_TOKEN_BUDGET,这里不再重复钉一遍
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
        f"本文件已用 autouse fixture 把 HISTORY_TOKEN_BUDGET 钉在 {PINNED_TOKEN_BUDGET},"
        "历史不可能被裁空 —— 所以若看到 [1, 1],"
        "只可能是两轮读到了同一份空历史:锁没盖住整轮读-改-写。"
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


async def test_session_is_still_usable_after_a_failed_turn(monkeypatch):
    """失败路径也必须把锁放掉 —— 否则该会话**永久死锁**:无异常、无日志、无超时。

    这是并发那条 `[1, 3]` 断言守的同一份保证的另一半:能串行,也得能解锁。
    把 `async with` 换成"手动 acquire + 只在成功路径 release"后,
    `test_model_exception_propagates_to_caller` 照样绿,只有本用例会红。
    """
    service.store = SessionStore()

    class FlakyOnce:
        """第一轮断上游,第二轮正常 —— 一次失败不该毒死整个会话。"""

        def __init__(self):
            self.calls = 0

        async def astream(self, messages):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("上游断了")
            yield AIMessageChunk(content="第二次成功")

    # 实例建在 lambda 外面:lambda 每次调用都 new 一个的话,计数器会被重置,
    # 第二轮又会走失败分支
    flaky = FlakyOnce()
    monkeypatch.setattr(service, "get_chat_model", lambda **kw: flaky)

    with pytest.raises(RuntimeError, match="上游断了"):
        await collect("s1", "第一轮")

    # 必须给超时:锁没释放时这行会**永久挂住**(本仓库没装 pytest-timeout,挂住比红更糟,
    # 整个套件会一直卡到 CI 的全局超时)。2 秒是乐观路径(~毫秒级)的千倍以上,
    # 慢机器上不会误判,真死锁时又能干净地报 TimeoutError,而不是把套件挂死。
    text, history = await asyncio.wait_for(collect("s1", "第二轮"), timeout=2.0)
    assert text == "第二次成功"
    # 失败的那轮没留下任何痕迹
    assert [m.content for m in history] == ["第二轮", "第二次成功"]


@pytest.mark.parametrize(
    "stream",
    [
        pytest.param("no_chunks", id="一个块都没有"),
        pytest.param("empty_chunks", id="只有空 text 的块"),
        pytest.param("whitespace_chunks", id="只有空白的块"),
    ],
)
async def test_empty_completion_is_a_failed_turn(monkeypatch, stream):
    """空回复按失败处理,不写回历史。

    写回 `AIMessage(content="")` 的代价不只是"这条难看":它会成为该会话后续**每一轮**
    都重放的空 assistant 消息,而客户端只看得到"这轮一个 token 都没有",像个成功的空回答。
    spec §8 要求这类异常以 error 浮出来,不是静默丢掉。

    `只有空白的块` 那一档是判据的分水岭:纯空白串**不是 falsy**,
    `if not reply_text` 会放它过去,于是 `' \\n'` 照样进历史、照样永久重放 ——
    与空回复同害。判据必须看 strip 之后的文本。
    """
    service.store = SessionStore()

    class Silent:
        """上游没调工具/没吐字:一个块都不给 / 只给空 text 的块 / 只给空白块。"""

        def __init__(self, kind: str):
            self.kind = kind

        async def astream(self, messages):
            if self.kind == "no_chunks":
                return
            if self.kind == "whitespace_chunks":
                for piece in [" \n", "  "]:
                    yield AIMessageChunk(content=piece)
                return
            yield AIMessageChunk(content="")

    monkeypatch.setattr(service, "get_chat_model", lambda **kw: Silent(stream))

    with pytest.raises(RuntimeError, match="没有产出任何内容"):
        await collect("s1", "在吗")

    # 关键:历史**原封不动**,而不是 ['在吗', ''] 或 ['在吗', ' \n  ']
    assert await service.store.get("s1") == []


async def test_empty_text_chunks_are_not_yielded(monkeypatch):
    """夹带的空 text 块不上抛 —— 真实 OpenAI 兼容流会这么干。

    断言的是**产出块序列**,不是拼接后的文本:`"".join` 会把空串吃掉,
    所以只看拼接结果的话,把 `if chunk.text:` 去掉照样绿 —— 那样这条就测不到东西。
    """
    service.store = SessionStore()

    class ChunkedFake:
        async def astream(self, messages):
            for piece in ["您好,", "", "48 ", "", "", "小时内发货。"]:
                yield AIMessageChunk(content=piece)

    monkeypatch.setattr(service, "get_chat_model", lambda **kw: ChunkedFake())

    chunks = [c async for c in service.stream_chat("s1", "几点发货?")]
    # 空块一个都不许出现
    assert chunks == ["您好,", "48 ", "小时内发货。"]

    # 回写仍然拼出完整回复(空块参与累加,但不产出)
    history = await service.store.get("s1")
    assert history[1].content == "您好,48 小时内发货。"
