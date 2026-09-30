"""流式出口 —— 事件序列是这一层的对外契约。"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from mewhelp.ch02 import service
from mewhelp.ch02.events import DoneEvent, SessionEvent, TokenEvent, ToolEvent
from mewhelp.db.base import Base
from mewhelp.db.models import Conversation, Message, MsgRole
from mewhelp.db.seed import seed
from tests.fakes import FakeToolChatModel, text_chunks, tool_call_chunks


@pytest.fixture(autouse=True)
def isolated_query_understanding(monkeypatch):
    from tests.fakes import patch_query_understanding
    patch_query_understanding(monkeypatch)


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        seed(s)
        s.commit()
    return lambda: Session(engine)


async def collect(**kwargs) -> list:
    return [event async for event in service.stream_agent_turn(**kwargs)]


def patch_model(monkeypatch, model):
    monkeypatch.setattr(service, "get_chat_model", lambda **kw: model)
    return model


async def test_event_sequence_without_tools(session_factory, monkeypatch):
    """无工具:session → token* → done,**没有** tool 帧。"""
    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("您好", ",有什么可以帮您?")]))
    events = await collect(
        session_factory=session_factory, session_id="s1", user_id="u1", message="你好"
    )

    assert isinstance(events[0], SessionEvent)
    assert isinstance(events[-1], DoneEvent)
    tokens = "".join(e.text for e in events if isinstance(e, TokenEvent))
    assert tokens == "您好,有什么可以帮您?"
    assert not [e for e in events if isinstance(e, ToolEvent)]


async def test_event_sequence_with_tools(session_factory, monkeypatch):
    """有工具:session → tool(start) → tool(end) → token* → done。"""
    patch_model(monkeypatch, FakeToolChatModel(rounds=[
        tool_call_chunks("query_logistics", '{"order_id": "1001"}'),
        text_chunks("您的包裹", "已到杭州。"),
    ]))
    events = await collect(
        session_factory=session_factory, session_id="s1", user_id="u1",
        message="订单 1001 的物流到哪了",
    )

    assert isinstance(events[0], SessionEvent)
    tool_events = [e for e in events if isinstance(e, ToolEvent)]
    assert [e.phase for e in tool_events] == ["start", "end"]
    assert tool_events[0].name == "query_logistics"
    assert tool_events[0].ok is None          # start 时还没有结果
    assert tool_events[1].ok is True
    assert tool_events[1].elapsed_ms is not None

    tokens = "".join(e.text for e in events if isinstance(e, TokenEvent))
    assert tokens == "您的包裹已到杭州。"

    # tool 帧必须在 token 之前 —— 徽章要先挂上,正文才跟上
    assert events.index(tool_events[0]) < events.index(
        next(e for e in events if isinstance(e, TokenEvent))
    )
    assert isinstance(events[-1], DoneEvent)


async def test_one_tool_frame_pair_per_call(session_factory, monkeypatch):
    """一轮两个调用 → 两对 tool 帧。"""
    patch_model(monkeypatch, FakeToolChatModel(rounds=[
        tool_call_chunks("query_order", '{"order_id": "1001"}', call_id="c1", index=0)
        + tool_call_chunks("query_logistics", '{"order_id": "1001"}', call_id="c2", index=1),
        text_chunks("查到了。"),
    ]))
    events = await collect(
        session_factory=session_factory, session_id="s1", user_id="u1",
        message="订单 1001 的状态和物流",
    )

    starts = [e for e in events if isinstance(e, ToolEvent) and e.phase == "start"]
    ends = [e for e in events if isinstance(e, ToolEvent) and e.phase == "end"]
    assert [e.name for e in starts] == ["query_order", "query_logistics"]
    assert [e.name for e in ends] == ["query_order", "query_logistics"]


async def test_same_name_called_twice_keeps_start_and_end_in_step(
    session_factory, monkeypatch
):
    """同一个工具在一轮里被调两次时,start 与 end 必须**一一对应**。

    前端是按**名字**给徽章排队的:同名的那几个 start 进队,收到 end 就从头取一个
    (见 `index.html` 的 `badges`)。它凭什么对 —— 全押在"先 start 的先收尾"这条
    顺序上。顺序一破,页面会把**第一个调用的耗时标到第二个调用的徽章上**:
    徽章照样收尾、照样有 ms,是一份**看起来正常的错数据**。

    同名调用是真实问法:「订单 1001 和 1002 的物流到哪了」。
    """
    patch_model(monkeypatch, FakeToolChatModel(rounds=[
        tool_call_chunks("query_logistics", '{"order_id": "1001"}', call_id="c1", index=0)
        + tool_call_chunks("query_logistics", '{"order_id": "1002"}', call_id="c2", index=1),
        text_chunks("两个都在路上。"),
    ]))
    events = await collect(
        session_factory=session_factory, session_id="s1", user_id="u1",
        message="订单 1001 和 1002 的物流到哪了",
    )

    starts = [e for e in events if isinstance(e, ToolEvent) and e.phase == "start"]
    ends = [e for e in events if isinstance(e, ToolEvent) and e.phase == "end"]
    assert [e.name for e in starts] == ["query_logistics", "query_logistics"]
    # 名字分不出这两个调用 —— 能分出的是 args,所以断言落在 args 上
    assert [e.args for e in starts] == [e.args for e in ends]


async def test_convergence_does_not_bind_tools(session_factory, monkeypatch):
    """收敛那一步**没有**绑定工具 —— 单轮的结构性保证(spec §11)。

    判别力:把收敛改成也 bind_tools,这条会红(模型于是能发起第二轮工具调用)。
    这比在 prompt 里写"只准调一次"可靠得多 —— 后者是一句可被忽略的话,
    前者是一个不存在的接口。
    """
    model = patch_model(monkeypatch, FakeToolChatModel(rounds=[
        tool_call_chunks("query_logistics", '{"order_id": "1001"}'),
        text_chunks("已到杭州。"),
    ]))
    await collect(
        session_factory=session_factory, session_id="s1", user_id="u1",
        message="订单 1001 的物流",
    )

    # bind_tools 只被调用过一次(只有 turn1)
    assert model.bind_calls == 1


async def test_resumed_flag_reaches_the_session_event(session_factory, monkeypatch):
    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("答")] * 2))
    first = await collect(
        session_factory=session_factory, session_id="s1", user_id="u1", message="A"
    )
    second = await collect(
        session_factory=session_factory, session_id="s1", user_id="u1", message="B"
    )

    assert first[0].resumed is False
    assert second[0].resumed is True
    assert first[0].session_id == second[0].session_id == "s1"


async def test_persists_four_rows_for_a_tool_turn(session_factory, monkeypatch):
    patch_model(monkeypatch, FakeToolChatModel(rounds=[
        tool_call_chunks("query_logistics", '{"order_id": "1001"}'),
        text_chunks("已到杭州。"),
    ]))
    await collect(
        session_factory=session_factory, session_id="s1", user_id="u1",
        message="订单 1001 的物流",
    )

    with session_factory() as s:
        rows = s.scalars(select(Message).order_by(Message.id)).all()
    assert [r.role for r in rows] == [
        MsgRole.user, MsgRole.assistant, MsgRole.tool, MsgRole.assistant
    ]
    # 这一轮的 turn1 正文是空的 —— 但空正文**本身是合法的**,不是被判空的理由;
    # 上游带 tool_calls 时**常常同时给一段前言**(实测 "I'll look up the logistics
    # information for order 1001."),那种情况这一格应该是那段前言。见下面
    # test_preamble_... 那条。
    assert rows[1].content is None
    assert rows[1].tool_calls[0]["name"] == "query_logistics"
    assert rows[3].content == "已到杭州。"


async def test_persists_two_rows_for_a_plain_turn(session_factory, monkeypatch):
    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("您好")]))
    await collect(
        session_factory=session_factory, session_id="s1", user_id="u1", message="你好"
    )

    with session_factory() as s:
        assert s.query(Message).count() == 2


async def test_empty_final_answer_raises_but_a_plain_turn_is_fine(session_factory, monkeypatch):
    """turn1 的空正文**合法**,收敛那步的空正文**非法** —— 本章最容易搞反的一处。

    turn1 调工具时正文通常就是空的;若把这两个判空写成同一个,工具调用轮
    会整轮失败,而那是本章的主路径。
    """
    from mewhelp.ch01.service import EmptyCompletionError

    patch_model(monkeypatch, FakeToolChatModel(rounds=[
        tool_call_chunks("query_logistics", '{"order_id": "1001"}'),
        text_chunks("   "),          # 收敛后只有空白
    ]))

    with pytest.raises(EmptyCompletionError):
        await collect(
            session_factory=session_factory, session_id="s1", user_id="u1",
            message="订单 1001 的物流",
        )


async def test_nothing_is_persisted_when_the_final_answer_is_empty(session_factory, monkeypatch):
    """空最终回答 → 不落库。与 ch01「失败的一轮不写」同一条规则。"""
    from mewhelp.ch01.service import EmptyCompletionError

    patch_model(monkeypatch, FakeToolChatModel(rounds=[
        tool_call_chunks("query_logistics", '{"order_id": "1001"}'),
        text_chunks(""),
    ]))

    with pytest.raises(EmptyCompletionError):
        await collect(
            session_factory=session_factory, session_id="s1", user_id="u1",
            message="订单 1001 的物流",
        )

    with session_factory() as s:
        assert s.query(Message).count() == 0
        # 但会话壳留着 —— 客户端下一轮带着同一个 session_id 回来是 resumed=True
        assert s.scalars(select(Conversation)).one().session_id == "s1"


# ---------- 会话 id 生成与整轮串行(这两件事归出口,不归 _prepare_turn)----------


async def test_generates_a_session_id_when_none_is_given(session_factory, monkeypatch):
    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("您好")]))
    events = await collect(
        session_factory=session_factory, session_id=None, user_id="demo-user", message="在吗"
    )

    first = events[0]
    assert isinstance(first, SessionEvent)
    assert first.session_id                    # 服务端生成了一个
    assert first.resumed is False
    with session_factory() as s:
        assert s.scalars(select(Conversation)).one().session_id == first.session_id


async def test_concurrent_turns_on_the_same_session_are_serialized(session_factory, monkeypatch):
    """Review Focus #1:同一 session 的并发两轮必须串行。

    用户双击发送、或两个标签页同一个会话时会撞上。不串行的话两轮各自读到
    同一份历史、各自收敛、再各写各的 —— **落库顺序交错**,而两轮都以为自己
    接住了上下文。ch01 是靠 `store.lock(session_id)` 解决的,本章沿用同一把锁。

    **锁的位置是这条测试真正在守的东西。** 把锁挪进 `_prepare_turn`,本用例照样绿
    (它测的是出口);但那样锁只罩住半轮,收敛与落库都在锁外 —— 上面那段失效的
    顺序完全没被防住。所以锁必须罩住「读历史 → 收敛 → 落库」整段。
    """
    import asyncio

    order: list[str] = []

    class Slow(FakeToolChatModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            order.append("enter")
            await asyncio.sleep(0.05)
            async for chunk in super()._astream(messages, stop, run_manager, **kwargs):
                yield chunk
            order.append("exit")

    patch_model(monkeypatch, Slow(rounds=[text_chunks("A答"), text_chunks("B答")]))

    async def one(message: str):
        return await collect(
            session_factory=session_factory, session_id="s1", user_id="u1", message=message
        )

    await asyncio.gather(one("A"), one("B"))

    # 串行的话必然是 enter/exit/enter/exit;并行会交错成 enter/enter/...
    assert order == ["enter", "exit", "enter", "exit"]


async def test_the_second_turn_sees_the_first_turns_answer(session_factory, monkeypatch):
    """串行的**目的**是这条:第二轮真的看得见第一轮。

    只断上面那条的 enter/exit 顺序,守的是"锁生效了";这条守的是"锁有用"。
    两条都要 —— 顺序对但历史没读对的话,串行只是白等。
    """
    seen: list[list] = []

    class Capturing(FakeToolChatModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            seen.append(messages)
            async for chunk in super()._astream(messages, stop, run_manager, **kwargs):
                yield chunk

    patch_model(monkeypatch, Capturing(rounds=[text_chunks("第一答"), text_chunks("第二答")]))

    await collect(
        session_factory=session_factory, session_id="s1", user_id="u1", message="第一问"
    )
    await collect(
        session_factory=session_factory, session_id="s1", user_id="u1", message="第二问"
    )

    # [0] 是 system prompt,与契约无关,从 [1:] 起比;顺序敏感,错序也要红
    assert [m.content for m in seen[1][1:]] == ["第一问", "第一答", "第二问"]


async def test_the_lock_covers_the_whole_tool_turn(session_factory, monkeypatch):
    """锁罩住的是**整轮**,不只是读历史那一半 —— 上面那条用例证明不了这件事。

    上面那条走的是**无工具**轮次:无工具时所有模型调用都发生在 `_prepare_turn`
    里面,把锁收窄到只包 `_prepare_turn`,它照样绿。Focus #1 真正要防的那一段
    —— 收敛与落库 —— 恰好在 `_prepare_turn` **外面**,只有**带工具**的轮次才碰得到。

    这条不靠时序,靠**事件归属**:把锁的进出、两次模型调用、落库都记进同一条
    时间线,再断言两个轮次各自成块、互不夹花。锁被收窄的话,第二轮会在第一轮
    收敛之前就挤进来,块就散了。

    刻意的对照:同一次运行里 `test_concurrent_turns_on_the_same_session_are_serialized`
    仍然是绿的 —— 它守的是"锁生效了",这条守的是"锁罩得够长"。
    """
    import asyncio

    from langchain_core.messages import ToolMessage

    from mewhelp.ch02 import service as service_module

    timeline: list[tuple[str, str]] = []

    def user_text(messages) -> str:
        return next(
            (m.content for m in reversed(messages) if m.type == "human"), "?"
        )

    class ToolThenAnswer(FakeToolChatModel):
        """按**消息内容**决定这一步干什么:见到 ToolMessage 就收敛,否则定工具。

        不用 `rounds` 队列 —— 两个并发轮次共用队列时,消费顺序本身就在被测范围内,
        一乱就分不清是"锁坏了"还是"剧本被吃串了"。自路由没这个问题。
        """

        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            from langchain_core.outputs import ChatGenerationChunk

            tag = user_text(messages)
            converging = any(isinstance(m, ToolMessage) for m in messages)
            timeline.append((tag, "收敛" if converging else "定工具"))
            await asyncio.sleep(0.02)
            if converging:
                for chunk in text_chunks(f"{tag} 的答复"):
                    yield ChatGenerationChunk(message=chunk)
            else:
                for chunk in tool_call_chunks(
                    "query_order", '{"order_id": "1001"}', call_id=f"c-{tag}", index=0
                ):
                    yield ChatGenerationChunk(message=chunk)

    patch_model(monkeypatch, ToolThenAnswer())

    real_persist = service_module.persist_turn

    def recording_persist(factory, prepared, *, answer):
        timeline.append((user_text(prepared.messages), "落库"))
        return real_persist(factory, prepared, answer=answer)

    monkeypatch.setattr(service_module, "persist_turn", recording_persist)

    # 锁的进出也记进同一条时间线 —— 这样"某轮的事件落在另一轮的锁区间里"
    # 就是一条可以直接断言的顺序事实,不依赖 sleep 的长短。
    real_lock = service_module.store.lock

    def recording_lock(session_id):
        # 真正那把锁的上下文管理器只取**一次** —— 每调一次 `real_lock` 拿到的
        # 是另一个对象,进出两次就会锁在两个不同的东西上,时序断言失去意义。
        cm = real_lock(session_id)

        class Locked:
            async def __aenter__(self):
                timeline.append(("<锁>", f"{session_id} 进"))
                return await cm.__aenter__()

            async def __aexit__(self, *exc):
                timeline.append(("<锁>", f"{session_id} 出"))
                return await cm.__aexit__(*exc)

        return Locked()

    monkeypatch.setattr(service_module.store, "lock", recording_lock)

    # 用一个别的用例不会碰的 session_id:store 的锁是**按 id 缓存**的,复用别的用例
    # 用过的 id 会拿到一把绑在别的事件循环上的旧锁,pytest-asyncio 下报
    # "bound to a different event loop" —— 那与本条要验的顺序毫无关系。
    await asyncio.gather(
        collect(
            session_factory=session_factory, session_id="lock-scope", user_id="u1", message="甲"
        ),
        collect(
            session_factory=session_factory, session_id="lock-scope", user_id="u1", message="乙"
        ),
    )

    # 每一轮自己的四件事,按发生顺序
    assert [e for e in timeline if e[0] == "甲"] == [
        ("甲", "定工具"), ("甲", "收敛"), ("甲", "落库")
    ], timeline
    assert [e for e in timeline if e[0] == "乙"] == [
        ("乙", "定工具"), ("乙", "收敛"), ("乙", "落库")
    ], timeline

    # 把同一轮的事件压成块:锁罩整轮的话正好两块,收窄就散成四块
    blocks: list[str] = []
    for who, _what in timeline:
        if who == "<锁>":
            continue
        if not blocks or blocks[-1] != who:
            blocks.append(who)
    assert blocks in (["甲", "乙"], ["乙", "甲"]), f"轮次夹花了:{timeline}"


async def test_upstream_failure_propagates_to_the_api_layer(session_factory, monkeypatch):
    """上游挂了要**抛出去**,由 api 层翻成 error 帧 —— service 不管传输格式。"""
    from langchain_core.outputs import ChatGenerationChunk

    class Boom(FakeToolChatModel):
        async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
            # 必须吐 ChatGenerationChunk,不是裸的 AIMessageChunk:
            # `_astream` 是**内层**钩子,`BaseChatModel.astream` 才是把它翻成
            # AIMessageChunk 的那一层。吐错类型会在 pydantic 里炸成
            # "'AIMessageChunk' object has no attribute 'message'",
            # 而那条报错完全看不出是测试自己写错了。
            yield ChatGenerationChunk(message=text_chunks("部分")[0])
            raise RuntimeError("上游断了")

    patch_model(monkeypatch, Boom(rounds=[]))

    with pytest.raises(RuntimeError, match="上游断了"):
        await collect(
            session_factory=session_factory, session_id="s1", user_id="u1", message="在吗"
        )


# ---------- spec §11 ③④:token 边流边吐、start 帧在执行**之前** ----------
#
# 下面三条是**追加**的,计划里没有。计划把 turn1 整个攒完、跑完工具之后才产出
# 任何事件,于是:(a) 不调工具的轮次(多数轮次)整块一帧,ch01 的流式白做;
# (b) 调工具的轮次里 turn1 的前言被**整段丢掉**;(c) start 帧在工具跑完之后才到,
# 徽章挂出来时"正在查"已经结束了。
# spec §11 ③ 写的是"边流边吐 token",④ 写的是"推 start 帧 → 执行 → 推 end 帧"。
# 这三条就是把那三件事钉住。


async def test_plain_turn_tokens_come_out_in_pieces_not_one_block(session_factory, monkeypatch):
    """无工具轮次必须是**多帧** token,不是一帧整块(spec §11 ③ / 方案 (c))。

    计划里这条会红:它把 `prepared.ai.content` 整块吐成一个 TokenEvent。
    拼起来的字符串一样,所以只断言"拼接结果"的测试**分辨不出**这两种实现 ——
    必须断帧数。
    """
    patch_model(monkeypatch, FakeToolChatModel(rounds=[text_chunks("您好", ",", "有什么可以帮您?")]))
    events = await collect(
        session_factory=session_factory, session_id="s1", user_id="u1", message="你好"
    )

    tokens = [e.text for e in events if isinstance(e, TokenEvent)]
    assert tokens == ["您好", ",", "有什么可以帮您?"]


async def test_business_preamble_is_buffered_and_only_convergence_is_delivered(
    session_factory, monkeypatch
):
    """业务前言缓冲；工具执行后只交付依据工具结果收敛的正文。"""
    patch_model(monkeypatch, FakeToolChatModel(rounds=[
        text_chunks("让我查一下。") + tool_call_chunks("query_logistics", '{"order_id": "1001"}'),
        text_chunks("已到杭州。"),
    ]))
    events = await collect(
        session_factory=session_factory, session_id="s1", user_id="u1",
        message="订单 1001 的物流",
    )

    kinds = [
        ("token", e.text) if isinstance(e, TokenEvent)
        else ("tool", e.phase) if isinstance(e, ToolEvent)
        else ("other", None)
        for e in events
    ]
    assert kinds == [
        ("other", None),            # SessionEvent
        ("tool", "start"),
        ("tool", "end"),
        ("token", "已到杭州。"),
        ("other", None),            # DoneEvent
    ]


async def test_tool_start_frame_arrives_before_the_tool_finishes(session_factory, monkeypatch):
    """start 帧必须在**执行之前**到 —— 它存在的理由就是这个。

    徽章的意义是"它在查,不是卡住了"。start 帧若等执行完才发,徽章挂出来时
    查询早就结束了,这一帧就只剩事后记录的价值。

    做法:把 `run_all` 换成一个先睡 0.3 秒的版本,然后只取前两个事件 ——
    它们必须在 0.3 秒睡眠结束**之前**就拿到。
    """
    import asyncio

    patch_model(monkeypatch, FakeToolChatModel(rounds=[
        tool_call_chunks("query_logistics", '{"order_id": "1001"}'),
        text_chunks("已到杭州。"),
    ]))

    real_build = service.build_registry

    def slow_build(session_factory, conversation_id, **knowledge_dependencies):
        registry = real_build(session_factory, conversation_id, **knowledge_dependencies)
        original = registry.run_all

        async def slow_run_all(calls):
            await asyncio.sleep(0.3)
            return await original(calls)

        registry.run_all = slow_run_all
        return registry

    monkeypatch.setattr(service, "build_registry", slow_build)

    agen = service.stream_agent_turn(
        session_factory=session_factory, session_id="s1", user_id="u1",
        message="订单 1001 的物流",
    )
    try:
        first = await asyncio.wait_for(agen.__anext__(), timeout=0.25)
        second = await asyncio.wait_for(agen.__anext__(), timeout=0.25)
        rest = [e async for e in agen]
    finally:
        await agen.aclose()

    assert isinstance(first, SessionEvent)
    assert isinstance(second, ToolEvent) and second.phase == "start"
    assert second.ok is None
    assert "已到杭州。" == "".join(e.text for e in rest if isinstance(e, TokenEvent))
