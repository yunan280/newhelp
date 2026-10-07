import pytest
from sqlalchemy import select

from mewhelp.ch05.actions import create_confirmed_ticket
from mewhelp.ch05.schemas import TicketRequest
from mewhelp.db.models import Ticket, ToolAuditLog
from tests.ch05.test_actions import offer
from tests.ch08.test_ticket_graph import install_tools, preview
from tests.fakes import FakeToolChatModel, patch_query_understanding, text_chunks, tool_call_chunks


async def test_old_button_uses_shared_audited_engine(workflow_runtime, model_factory, session_factory, tmp_path):
    await install_tools(workflow_runtime, tmp_path, session_factory)
    suggested = await offer(workflow_runtime, model_factory)
    request = TicketRequest(session_id='actions', offer_id=suggested.offer_id,
        confirmed=True, description='客服态度问题', ticket_type='投诉')
    first = await create_confirmed_ticket(workflow_runtime, request)
    second = await create_confirmed_ticket(workflow_runtime, request)
    assert first.ticket_no == second.ticket_no and second.replayed
    with session_factory() as db:
        assert len(db.scalars(select(Ticket)).all()) == 1
        audit = db.scalars(select(ToolAuditLog)).one()
        assert audit.tool_call_id == request.offer_id and audit.status == '成功'


async def test_old_button_receipt_replay_preserves_current_preview(workflow_runtime, model_factory, session_factory, tmp_path):
    from mewhelp.ch08.confirmation import pending_ticket
    await install_tools(workflow_runtime, tmp_path, session_factory)
    suggested = await offer(workflow_runtime, model_factory)
    request = TicketRequest(session_id='actions', offer_id=suggested.offer_id,
        confirmed=True, description='客服态度问题', ticket_type='投诉')
    first = await create_confirmed_ticket(workflow_runtime, request)
    waiting = await preview(workflow_runtime, model_factory, session='actions')
    assert (await create_confirmed_ticket(workflow_runtime, request)).ticket_no == first.ticket_no
    pending = await pending_ticket(workflow_runtime, 'actions', 'demo-user')
    assert pending.ticket_preview['confirmation_id'] == waiting.ticket_preview['confirmation_id']


@pytest.mark.parametrize('name,args,status', [
    ('create_ticket', {'description':'键盘坏了', 'ticket_type':'售后'}, '权限拒绝'),
    ('query_order', {'order_id':1001}, '校验拦下'),
    ('query_order', {'order_id':'1001'}, '成功'),
])
async def test_ch02_all_calls_use_audited_engine(workflow_runtime, session_factory, tmp_path, monkeypatch, name, args, status):
    import json

    from mewhelp.ch02 import service
    tools = await install_tools(workflow_runtime, tmp_path, session_factory)
    patch_query_understanding(monkeypatch)
    model = FakeToolChatModel(rounds=[tool_call_chunks(name, json.dumps(args), call_id='legacy1'), text_chunks('如实说明工具结果')])
    monkeypatch.setattr(service, 'get_chat_model', lambda **kw:model)
    result = await service.run_agent_turn(session_factory, session_id='ch02-audit',
        user_id='demo-user', message='查订单1001', tool_runtime=tools)
    assert result.tool_results[0].status == status
    with session_factory() as db:
        assert not db.scalars(select(Ticket)).all()
        audit = db.scalars(select(ToolAuditLog)).one()
        assert audit.tool_call_id == 'legacy1' and audit.status == status


async def test_tool_audit_call_id_links_to_message_ledger(workflow_runtime, model_factory, session_factory, tmp_path):
    from mewhelp.ch08.confirmation import resume_ticket
    from mewhelp.ch08.schemas import TicketResumeRequest
    from mewhelp.db.models import Message, MsgRole
    await install_tools(workflow_runtime, tmp_path, session_factory)
    waiting = await preview(workflow_runtime, model_factory)
    await resume_ticket(workflow_runtime, TicketResumeRequest(session_id=waiting.session_id,
        confirmation_id=waiting.ticket_preview['confirmation_id'], action='confirm'))
    with session_factory() as db:
        audit = db.scalars(select(ToolAuditLog)).one()
        observation = db.scalars(select(Message).where(Message.role == MsgRole.tool)).one()
        assert audit.tool_call_id == observation.tool_call_id == 'ticket1'


async def test_ticket_number_failure_is_not_reported_as_success(workflow_runtime, session_factory, tmp_path):
    import datetime as dt

    from mewhelp.db.models import Conversation, TicketType
    from mewhelp.tools.contracts import ToolCallContext, WriteAuthorization
    from mewhelp.tools.permissions import arguments_hash
    tools = await install_tools(workflow_runtime, tmp_path, session_factory)
    with session_factory() as db:
        owner = Conversation(session_id='number-collision', user_id='demo-user')
        db.add(owner)
        db.flush()
        cid = owner.id
        for suffix in ('001', '003'):
            db.add(Ticket(ticket_no=f'T{dt.date.today():%Y%m%d}{suffix}', conversation_id=cid,  # noqa: DTZ011 — 编号沿用本地日期
                description='占位', ticket_type=TicketType.consult))
        db.commit()
    args = {'description':'新问题', 'ticket_type':'咨询'}
    auth = WriteAuthorization('legacy_button', 'collision', cid, 'number-collision', 'demo-user', 'create_ticket', arguments_hash(args))
    result = await tools.engine.execute(await tools.refresh(), 'create_ticket', args,
        ToolCallContext(cid, 'number-collision', 'demo-user', authorization=auth))
    assert not result.ok and result.status == '失败' and result.retry_count == 0
