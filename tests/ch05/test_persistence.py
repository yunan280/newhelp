import asyncio

import pytest

from mewhelp.ch05.schemas import TurnRequest
from mewhelp.db.models import Conversation, Message, MsgRole


async def test_file_reopen_preserves_history_and_isolates_sessions(
    session_factory, checkpoint_settings, model_factory
):
    try:
        from mewhelp.ch05.runtime import open_runtime
        from mewhelp.ch05.service import run_turn
    except ImportError:
        pytest.fail("file checkpointer runtime missing")
    async with open_runtime(
        session_factory, settings=checkpoint_settings, model_factory=model_factory
    ) as runtime:
        await run_turn(runtime, TurnRequest(message="你好", session_id="persist"))
    async with open_runtime(
        session_factory, settings=checkpoint_settings, model_factory=model_factory
    ) as runtime:
        result = await run_turn(runtime, TurnRequest(message="谢谢", session_id="persist"))
        assert result.resumed
        same = await runtime.graph.aget_state({"configurable": {"thread_id": "persist"}})
        other = await runtime.graph.aget_state({"configurable": {"thread_id": "other"}})
        assert len(same.values["messages"]) == 4 and other.values == {}


async def test_existing_mysql_history_bootstraps_once(workflow_runtime, session_factory):
    from mewhelp.ch05.service import run_turn

    with session_factory() as db:
        conv = Conversation(session_id="old", user_id="demo-user")
        db.add(conv)
        db.flush()
        db.add_all(
            [
                Message(conversation_id=conv.id, role=MsgRole.user, content="旧问题"),
                Message(conversation_id=conv.id, role=MsgRole.assistant, content="旧答案"),
            ]
        )
        db.commit()
    for _ in range(2):
        await run_turn(workflow_runtime, TurnRequest(message="你好", session_id="old"))
    snapshot = await workflow_runtime.graph.aget_state({"configurable": {"thread_id": "old"}})
    assert len(snapshot.values["messages"]) == 6


async def test_failed_turn_never_enters_completed_history(workflow_runtime, model_factory):
    from mewhelp.ch05.service import run_turn

    model_factory.fail_classifier = True
    with pytest.raises(RuntimeError):
        await run_turn(workflow_runtime, TurnRequest(message="坏的问题", session_id="fail"))
    model_factory.fail_classifier = False
    await run_turn(workflow_runtime, TurnRequest(message="你好", session_id="fail"))
    snapshot = await workflow_runtime.graph.aget_state({"configurable": {"thread_id": "fail"}})
    assert [m.content for m in snapshot.values["messages"] if m.type == "human"] == ["你好"]


async def test_client_closes_partial_stream_without_completed_history(
    workflow_runtime, model_factory
):
    from mewhelp.ch05.service import stream_turn

    model_factory.intents = ["订单"]
    model_factory.block_answer = asyncio.Event()
    stream = stream_turn(
        workflow_runtime,
        TurnRequest(message="订单1001", session_id="cancel"),
        entry_point="chat_stream",
    )
    while (await anext(stream))["event"] != "token":
        pass
    await asyncio.wait_for(stream.aclose(), 2)
    snapshot = await workflow_runtime.graph.aget_state({"configurable": {"thread_id": "cancel"}})
    assert snapshot.values.get("messages", []) == []
