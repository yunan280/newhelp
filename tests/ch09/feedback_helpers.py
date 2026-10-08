from mewhelp.ch05.workflow import _event_key
from mewhelp.ch09.contracts import MessageSnapshot
from mewhelp.db.models import Message, MsgRole


def seed_answer(
    factory,
    cid,
    *,
    user_id=10,
    answer_id=11,
    turn="t",
    question="同一个原始问题",
    evidence=None,
    stored=True,
    answer_status="completed",
):
    snapshot = MessageSnapshot(
        turn_id=turn,
        source_user_event_key=_event_key(turn, "user", 0),
        source_user_message_id=str(user_id),
        intent="商品咨询",
        retrieval_performed=evidence is not None,
        retrieved_chunks=evidence,
        answer_status=answer_status,
    )
    with factory() as db:
        db.add_all(
            [
                Message(
                    id=user_id,
                    conversation_id=cid,
                    role=MsgRole.user,
                    content=question,
                    ch06_event_key=snapshot.source_user_event_key,
                ),
                Message(
                    id=answer_id,
                    conversation_id=cid,
                    role=MsgRole.assistant,
                    content="同样的答复",
                    ch06_event_key=_event_key(turn, "complete", "answer"),
                    retrieval_snapshot=snapshot.model_dump(mode="json") if stored else None,
                ),
            ]
        )
        db.commit()
    return snapshot


def evidence(text):
    return {
        "state": "captured",
        "chunks": [
            {
                "rank": 1,
                "chunk_id": "1",
                "text": text,
                "questions": "原问题",
                "answer": "原答案",
                "relevance_score": 0.8,
                "content_hash": "a" * 64,
            }
        ],
    }
