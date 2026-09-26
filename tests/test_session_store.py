"""SessionStore 的测试 —— 隔离性、副本语义、并发串行。"""

import asyncio

from langchain_core.messages import AIMessage, HumanMessage

from mewhelp.memory import SessionStore


async def test_get_on_unknown_session_returns_empty():
    assert await SessionStore().get("nope") == []


async def test_append_then_get_roundtrips():
    store = SessionStore()
    await store.append("s1", HumanMessage(content="你好"))
    msgs = await store.get("s1")
    assert [m.content for m in msgs] == ["你好"]


async def test_sessions_are_isolated():
    store = SessionStore()
    await store.append("s1", HumanMessage(content="一"))
    await store.append("s2", HumanMessage(content="二"))
    assert [m.content for m in await store.get("s1")] == ["一"]
    assert [m.content for m in await store.get("s2")] == ["二"]


async def test_append_keeps_every_message_when_one_call_carries_several():
    """一轮对话的真实调用形状是 `append(sid, human, reply)` —— 一次写两条。

    这条专门钉住变参语义:只保留最后一条(例如 `extend(messages[-1:])`)时它必红。
    漏掉人类那条的症状极其隐蔽:长度断言照过,但模型永远看不到用户刚说的话。
    """
    store = SessionStore()
    human = HumanMessage(content="我要退款")
    reply = AIMessage(content="好的,请提供订单号")
    await store.append("s1", human, reply)

    msgs = await store.get("s1")
    assert [type(m) for m in msgs] == [HumanMessage, AIMessage]
    assert [m.content for m in msgs] == ["我要退款", "好的,请提供订单号"]
    assert len(await store.get("s1")) == 2


async def test_get_returns_a_copy_so_callers_cannot_mutate_internal_state():
    store = SessionStore()
    await store.append("s1", HumanMessage(content="一"))
    got = await store.get("s1")
    got.append(HumanMessage(content="偷加的"))
    assert len(await store.get("s1")) == 1


async def test_clear_removes_the_session():
    store = SessionStore()
    await store.append("s1", HumanMessage(content="一"))
    await store.clear("s1")
    assert await store.get("s1") == []


# 注意:名字叫 concurrent,但它**不是**并发覆盖,别拿它当并发证据。
#
# 它实际覆盖的只有「顺序追加 50 条不丢」这一件事,原因有两个:
#
# 1. 锁是**测试自己**拿的;而 `append()` 内部根本不取锁(按设计由 service 层
#    持锁包住一整轮对话),所以这把锁对被测行为毫无约束。
# 2. `append()` 体内没有 await,协程一旦被 await 就一口气跑完、不让出事件循环,
#    50 次追加必然顺序执行。
#
# 因此它**测不出锁坏掉** —— 把 `lock()` 改成每次返回一把新锁,这条依然绿。
# 它也不是恒真断言(若 `append` 变成 no-op 它会红,但那与并发无关),
# 只是它证明不了并发。真正验证并发语义的是下面那条
# `test_lock_prevents_two_turns_from_reading_the_same_stale_history`。
async def test_concurrent_appends_do_not_lose_messages():
    store = SessionStore()
    async with store.lock("s1"):
        await asyncio.gather(
            *(store.append("s1", HumanMessage(content=str(i))) for i in range(50))
        )
    assert len(await store.get("s1")) == 50


async def test_same_session_lock_serializes_turns():
    """同一会话的两个请求必须一前一后,不能交错 —— 交错会让两轮都读到同一份旧历史。"""
    store = SessionStore()
    order: list[str] = []

    async def turn(name: str, hold: float) -> None:
        async with store.lock("s1"):
            order.append(f"{name}-enter")
            await asyncio.sleep(hold)
            order.append(f"{name}-exit")

    await asyncio.gather(turn("a", 0.05), turn("b", 0.0))
    assert order == ["a-enter", "a-exit", "b-enter", "b-exit"]


async def test_different_sessions_do_not_block_each_other():
    store = SessionStore()
    order: list[str] = []

    async def turn(session: str, name: str, hold: float) -> None:
        async with store.lock(session):
            order.append(f"{name}-enter")
            await asyncio.sleep(hold)
            order.append(f"{name}-exit")

    await asyncio.gather(turn("s1", "slow", 0.05), turn("s2", "fast", 0.0))
    # fast 在另一个会话,不该被 slow 挡住
    assert order == ["slow-enter", "fast-enter", "fast-exit", "slow-exit"]


async def test_lock_is_stable_for_the_same_session():
    store = SessionStore()
    assert store.lock("s1") is store.lock("s1")


async def test_lock_prevents_two_turns_from_reading_the_same_stale_history():
    """真·并发用例:没有锁时两轮会并排走完"读-改-写",第二轮看不到第一轮的消息。

    append 本身不加锁 —— 串行由 service 层用 `async with store.lock(...)`
    包住**一整轮**对话来保证。这里把一轮对话写成"读历史 → 等模型 → 写回",
    用来验证那把锁确实覆盖了整轮,而不是只护住了最后一次写入。
    """
    store = SessionStore()
    history_sizes: list[int] = []

    async def turn(n: int) -> None:
        async with store.lock("s1"):
            history_sizes.append(len(await store.get("s1")))  # 读:本轮开始时的历史
            await asyncio.sleep(0.01)  # 等上游模型出结果
            await store.append("s1", HumanMessage(content=f"问{n}"))
            await store.append("s1", AIMessage(content=f"答{n}"))

    await asyncio.gather(turn(1), turn(2))
    # 第二轮必须看得见第一轮的两条消息;否则两轮各自基于同一份旧历史作答
    assert history_sizes == [0, 2]
    assert [m.content for m in await store.get("s1")] == ["问1", "答1", "问2", "答2"]
