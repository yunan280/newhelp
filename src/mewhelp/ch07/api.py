from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from mewhelp.ch05.api import RuntimeDep

from .schemas import ConversationList, ConversationMessages
from .store import list_user_conversations, read_visible_messages

router = APIRouter(prefix='/api/conversations', tags=['conversations'])
UserQuery = Annotated[str, Query(min_length=1, max_length=64)]


def owner(user_id: str) -> str:
    if not user_id.strip():
        raise HTTPException(status_code=422, detail='user_id 不能为空白')
    return user_id.strip()


@router.get('', response_model=ConversationList)
def conversations(runtime: RuntimeDep, user_id: UserQuery = 'demo-user') -> ConversationList:
    with runtime.context.session_factory() as session:
        return ConversationList(conversations=list_user_conversations(session, user_id=owner(user_id)))


@router.get('/{conversation_id}/messages', response_model=ConversationMessages)
def messages(conversation_id: int, runtime: RuntimeDep,
             user_id: UserQuery = 'demo-user') -> ConversationMessages:
    try:
        with runtime.context.session_factory() as session:
            return read_visible_messages(session, conversation_id=conversation_id, user_id=owner(user_id))
    except LookupError as error:
        raise HTTPException(status_code=404, detail='会话不存在') from error
