import pytest
from feedback_helpers import seed_answer
from langchain_core.messages import AIMessage

from mewhelp.db.models import Message


def checkpoint(user_id, answer_id, turn="t", **values):
    message = AIMessage(
        "同样的答复",
        id=turn + "-answer",
        additional_kwargs={
            "ch07": {
                "ledger_id": answer_id,
                "from_msg_id": user_id,
                "turn_id": turn,
                "committed": True,
            }
        },
    )
    return {"messages": [message], "turn_id": turn, **values}


@pytest.mark.asyncio
async def test_old_answer_bigint_binding_does_not_pick_latest_same_question(generation_context):
    from mewhelp.ch09.feedback import recover_answer_snapshot

    ctx, cid = generation_context
    large = 2**53 + 10
    seed_answer(ctx.session_factory, cid, user_id=large, answer_id=large + 1, stored=False)
    seed_answer(
        ctx.session_factory,
        cid,
        user_id=large + 2,
        answer_id=large + 3,
        stored=False,
        turn="second",
    )
    old = checkpoint(large, large + 1, route="knowledge", evidence={"sources": [], "scores": []})
    latest = checkpoint(large + 2, large + 3, turn="second", route="business")

    async def reader(session_id):
        return [latest, old]

    recovered = await recover_answer_snapshot(
        large + 1, factory=ctx.session_factory, checkpoint_reader=reader
    )
    assert recovered.source_user_message_id == str(large)
    assert recovered.retrieved_chunks.state == "empty"


@pytest.mark.asyncio
async def test_bound_old_citations_have_no_invented_score(generation_context):
    from mewhelp.ch09.feedback import recover_answer_snapshot

    ctx, cid = generation_context
    seed_answer(ctx.session_factory, cid, stored=False)
    with ctx.session_factory() as db:
        db.get(Message, 11).citations = [
            {"chunk_id": "1", "questions": "旧问题", "answer": "旧完整答案", "category": "旧分类"}
        ]
        db.commit()

    async def reader(session_id):
        return [checkpoint(10, 11, turn="t", route="unknown")]

    recovered = await recover_answer_snapshot(
        11, factory=ctx.session_factory, checkpoint_reader=reader
    )
    assert recovered.retrieved_chunks.state == "legacy_partial"
    assert recovered.retrieved_chunks.chunks[0].relevance_score is None


@pytest.mark.asyncio
@pytest.mark.parametrize("performed,expected", [(False, None), (None, "unavailable")])
async def test_no_retrieval_and_unknown_are_distinct(generation_context, performed, expected):
    from mewhelp.ch09.feedback import recover_answer_snapshot

    ctx, cid = generation_context
    seed_answer(ctx.session_factory, cid, stored=False)

    async def reader(session_id):
        return [checkpoint(10, 11, retrieval_performed=performed)]

    recovered = await recover_answer_snapshot(
        11, factory=ctx.session_factory, checkpoint_reader=reader
    )
    assert (recovered.retrieved_chunks.state if recovered.retrieved_chunks else None) == expected


@pytest.mark.asyncio
async def test_unbound_old_answer_does_not_guess_nearest_user(generation_context):
    from mewhelp.ch09.feedback import recover_answer_snapshot

    ctx, cid = generation_context
    seed_answer(ctx.session_factory, cid, stored=False)

    async def reader(session_id):
        return [{"messages": [AIMessage("同样的答复")]}]

    with pytest.raises(ValueError, match="绑定"):
        await recover_answer_snapshot(11, factory=ctx.session_factory, checkpoint_reader=reader)


async def test_legacy_preview_cannot_become_feedback(generation_context):
    from mewhelp.ch09.feedback import recover_answer_snapshot

    ctx, cid = generation_context
    seed_answer(ctx.session_factory, cid, stored=False)
    state = checkpoint(10, 11)
    state['messages'][0].id = 't-waiting-ticket'

    async def reader(session_id):
        return [state]

    with pytest.raises(ValueError, match='最终回答'):
        await recover_answer_snapshot(11, factory=ctx.session_factory, checkpoint_reader=reader)
