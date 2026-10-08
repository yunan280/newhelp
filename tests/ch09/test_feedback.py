import pytest
from feedback_helpers import evidence, seed_answer
from sqlalchemy import event, select

from mewhelp.db.models import Message, MsgRole
from mewhelp.knowledge.refusals import LowConfidenceQuestion, PoolCommitError


def request(answer="11", **overrides):
    from mewhelp.ch09.feedback import FeedbackRequest

    return FeedbackRequest(
        **(
            {"session_id": "s", "user_id": "u", "answer_message_id": answer, "choice": "down"}
            | overrides
        )
    )


@pytest.mark.asyncio
async def test_exact_answer_bigint_and_repeated_question_use_original_turn(generation_context):
    from mewhelp.ch09.feedback import submit_negative_feedback

    ctx, cid = generation_context
    large = 2**53 + 101
    seed_answer(
        ctx.session_factory,
        cid,
        user_id=large,
        answer_id=large + 1,
        evidence=evidence("第一轮原片段"),
    )
    seed_answer(
        ctx.session_factory,
        cid,
        user_id=large + 2,
        answer_id=large + 3,
        turn="second",
        evidence=evidence("第二轮原片段"),
    )
    result = await submit_negative_feedback(
        ctx.session_factory, request(str(large + 1)), recover=None
    )
    with ctx.session_factory() as db:
        row = db.get(LowConfidenceQuestion, int(result.pool_id))
        saved = db.get(Message, large + 1).retrieval_snapshot
        assert saved["source_user_message_id"] == str(large)
        assert (
            row.original_question == "同一个原始问题"
            and row.retrieved_chunks["chunks"][0]["text"] == "第一轮原片段"
        )
        assert (row.entry_point, row.trigger_stage, row.reason_code) == (
            "feedback",
            "feedback",
            "user_feedback",
        )
    assert result.answer_message_id == str(large + 1)


@pytest.mark.asyncio
async def test_retry_returns_same_pool_id(generation_context):
    from mewhelp.ch09.feedback import submit_negative_feedback

    ctx, cid = generation_context
    seed_answer(ctx.session_factory, cid)
    first = await submit_negative_feedback(ctx.session_factory, request(), recover=None)
    replay = await submit_negative_feedback(ctx.session_factory, request(), recover=None)
    assert replay.pool_id == first.pool_id and replay.replayed and not first.replayed
    with ctx.session_factory() as db:
        assert len(db.scalars(select(LowConfidenceQuestion)).all()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [{"user_id": "another"}, {"session_id": "another"}])
async def test_cross_owner_is_indistinguishable_from_missing(generation_context, overrides):
    from mewhelp.ch09.feedback import submit_negative_feedback

    ctx, cid = generation_context
    seed_answer(ctx.session_factory, cid)
    with pytest.raises(LookupError, match="回答不存在"):
        await submit_negative_feedback(ctx.session_factory, request(**overrides), recover=None)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["tool", "control", "waiting"])
async def test_non_final_messages_are_not_feedback_targets(generation_context, kind):
    from mewhelp.ch09.feedback import submit_negative_feedback

    ctx, cid = generation_context
    seed_answer(
        ctx.session_factory, cid, answer_status="waiting" if kind == "waiting" else "completed"
    )
    with ctx.session_factory() as db:
        row = db.get(Message, 11)
        if kind == "tool":
            row.role = MsgRole.tool
        if kind == "control":
            row.tool_calls = [{"id": "x", "name": "tool", "args": {}}]
        db.commit()
    with pytest.raises(ValueError, match="最终回答"):
        await submit_negative_feedback(ctx.session_factory, request(), recover=None)


@pytest.mark.asyncio
async def test_pool_and_feedback_marker_roll_back_together(generation_context):
    from mewhelp.ch09.feedback import submit_negative_feedback

    ctx, cid = generation_context
    seed_answer(ctx.session_factory, cid)
    engine = ctx.session_factory.kw["bind"]

    def fail_update(conn, cursor, statement, parameters, context, many):
        if statement.lstrip().upper().startswith("UPDATE MESSAGES"):
            raise RuntimeError("injected marker failure")

    event.listen(engine, "before_cursor_execute", fail_update)
    try:
        with pytest.raises(PoolCommitError):
            await submit_negative_feedback(ctx.session_factory, request(), recover=None)
    finally:
        event.remove(engine, "before_cursor_execute", fail_update)
    with ctx.session_factory() as db:
        assert db.scalar(select(LowConfidenceQuestion)) is None
        assert db.get(Message, 11).retrieval_snapshot["feedback_lcq_id"] is None
    assert not (
        await submit_negative_feedback(ctx.session_factory, request(), recover=None)
    ).replayed


@pytest.mark.parametrize("value", [11, 1.1, "01", "0", str(2**64)])
def test_feedback_ids_are_strict_unsigned_decimal_strings(value):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        request(value)


def test_history_exposes_persistent_answer_id_and_feedback_state(generation_context):
    from mewhelp.ch07.store import read_visible_messages

    ctx, cid = generation_context
    large = 2**53 + 77
    seed_answer(ctx.session_factory, cid, user_id=large, answer_id=large + 1)
    with ctx.session_factory() as db:
        row = db.get(Message, large + 1)
        row.retrieval_snapshot = {**row.retrieval_snapshot, "feedback_lcq_id": "9"}
        db.commit()
        result = read_visible_messages(db, conversation_id=cid, user_id="u")
    assert result.messages[-1].answer_message_id == str(large + 1)
    assert result.messages[-1].feedback_status == "down"
