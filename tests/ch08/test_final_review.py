import asyncio
from dataclasses import replace

import pytest
from langchain_core.tools import StructuredTool, tool
from sqlalchemy import select

from mewhelp.ch05.schemas import TurnRequest
from mewhelp.ch05.service import run_turn
from mewhelp.ch08.confirmation import resume_ticket
from mewhelp.ch08.schemas import TicketResumeRequest
from mewhelp.db.models import ToolAuditLog
from mewhelp.tools.audit import ToolAuditWriter
from mewhelp.tools.contracts import ToolCallContext, ToolSnapshot, ToolSpec
from mewhelp.tools.engine import ToolExecutionEngine
from mewhelp.tools.registry import ToolRegistry

from .test_ticket_graph import CONTROL, install_tools, preview, ticket_call


@pytest.mark.parametrize('change',['permission','schema'])
async def test_committed_receipt_recovery_precedes_current_write_rules(workflow_runtime,tmp_path,session_factory,model_factory,change):
    from langgraph.types import Command

    from mewhelp.ch07.context import prepare_request_context
    from mewhelp.db.models import Ticket, TicketType
    tools=await install_tools(workflow_runtime,tmp_path,session_factory)
    waiting=await preview(workflow_runtime,model_factory,session='committed-crash')
    config={'configurable':{'thread_id':waiting.session_id}}
    saved=await workflow_runtime.graph.aget_state(config)
    context=await prepare_request_context(workflow_runtime.context,saved.values)
    nonce=waiting.ticket_preview['confirmation_id']
    await workflow_runtime.graph.ainvoke(Command(resume={'action':'confirm','confirmation_id':nonce}),
        config,context=context,interrupt_before=['execute_confirmed_ticket'],durability='sync')
    with session_factory() as db:
        db.add(Ticket(conversation_id=waiting.conversation_id,ticket_no='T-committed',
            ticket_type=TicketType('售后'),description='键盘坏了',request_id=nonce))
        db.commit()
    if change=='permission':
        tools.settings.config_path.write_text('{"servers":{},"permissions":{},"builtin_permissions":{"create_ticket":"deny"}}')
    else:
        specs=dict(tools.registry.snapshot().specs)
        schema={'type':'object','properties':{'new_field':{'type':'string'}},'required':['new_field']}
        specs['create_ticket']=replace(specs['create_ticket'],input_schema=schema)
        tools.registry=ToolRegistry(specs,engine=tools.engine)
    result=await resume_ticket(workflow_runtime,TicketResumeRequest(session_id=waiting.session_id,confirmation_id=nonce,action='confirm'))
    assert result.ticket_receipt and result.ticket_receipt['ticket_no']=='T-committed'
    assert 'T-committed' in result.answer


@pytest.mark.parametrize('action', ['confirm','cancel','new_message'])
async def test_completed_or_abandoned_request_does_not_authorize_next_problem(workflow_runtime, tmp_path, session_factory, model_factory, action):
    await install_tools(workflow_runtime, tmp_path, session_factory)
    waiting = await preview(workflow_runtime, model_factory)
    if action != 'new_message':
        await resume_ticket(workflow_runtime, TicketResumeRequest(session_id=waiting.session_id,
            confirmation_id=waiting.ticket_preview['confirmation_id'], action=action))
    model_factory.decisions = [ticket_call('鼠标也坏了',call_id='next-problem'), CONTROL]
    result = await run_turn(workflow_runtime, TurnRequest(message='鼠标也坏了',session_id=waiting.session_id))
    assert result.ticket_preview is None
    saved = await workflow_runtime.graph.aget_state({'configurable':{'thread_id':waiting.session_id}})
    assert saved.values['ticket_request']['explicit_request'] is False


async def test_mcp_additional_properties_keeps_declared_value_type(audit_factory):
    schema={'type':'object','additionalProperties':{'type':'string'}}
    remote = StructuredTool.from_function(lambda **values: values, name='labels', description='查询标签', args_schema=schema)
    registry = ToolRegistry({'labels':ToolSpec(remote,source='mcp',mcp_server='logistics',permission='readonly')},
        engine=ToolExecutionEngine(ToolAuditWriter(audit_factory)))
    good=await registry.run('labels',{'label':'hello'})
    assert good.ok and 'hello' in good.content
    bad=await registry.run('labels',{'label':123})
    assert bad.status == '校验拦下'


def test_non_consuming_ref_cycle_is_rejected_but_recursive_tree_is_valid():
    from mewhelp.tools.validation import validate_arguments
    safe={'type':'object','properties':{'child':{'$ref':'#'}},'additionalProperties':False}
    tree=StructuredTool.from_function(lambda **values: values,name='tree',description='查询树',args_schema=safe)
    normalized=ToolRegistry({'tree':ToolSpec(tree,source='mcp',mcp_server='s')}).get('tree').input_schema
    assert not validate_arguments(normalized,{'child':{'child':{}}})
    assert validate_arguments(normalized,{'child':'invalid'})
    circular={'type':'object','$ref':'#'}
    remote=StructuredTool.from_function(lambda **values: values, name='tree',description='查询树',args_schema=circular)
    with pytest.raises(ValueError,match='循环'):
        ToolRegistry({'tree':ToolSpec(remote,source='mcp',mcp_server='s')})


async def test_validator_failure_is_structured_and_audited(audit_factory):
    @tool
    def query() -> str:
        """示例查询。"""
        pytest.fail('Invalid schema cannot execute handler')
    spec=replace(ToolRegistry({'query':ToolSpec(query)}).get('query'),input_schema={'type':'object','$ref':'#'})
    engine=ToolExecutionEngine(ToolAuditWriter(audit_factory))
    result=await engine.execute(ToolSnapshot({'query':spec},'bad-schema'),'query',{},ToolCallContext(tool_call_id='validator-crash'))
    assert result.status == '失败' and result.attempts == 0
    with audit_factory() as db:
        rows=db.scalars(select(ToolAuditLog)).all()
        assert len(rows)==1 and rows[0].tool_call_id=='validator-crash'


async def test_invalid_url_type_fails_closed_without_losing_builtins(workflow_runtime,tmp_path,session_factory):
    tools=await install_tools(workflow_runtime,tmp_path,session_factory)
    tools.settings.config_path.write_text('{"servers":{"x":{"url":123}},"permissions":{}}')
    snapshot=await tools.refresh()
    assert snapshot.get('query_order').permission=='readonly'
    assert snapshot.get('create_ticket').permission=='deny'
    assert tools.last_refresh['config_error']


async def test_cancel_during_backoff_preserves_one_audit(audit_factory):
    sleeping=asyncio.Event()
    async def backoff(_):
        sleeping.set()
        await asyncio.Event().wait()
    @tool
    async def query() -> str:
        """查询网络故障。"""
        raise ConnectionResetError('network unavailable')
    engine=ToolExecutionEngine(ToolAuditWriter(audit_factory),sleep=backoff)
    task=asyncio.create_task(engine.execute(ToolRegistry({'query':ToolSpec(query)}).snapshot(),
        'query',{},ToolCallContext(tool_call_id='cancel-backoff')))
    await asyncio.wait_for(sleeping.wait(),1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with audit_factory() as db:
        rows=db.scalars(select(ToolAuditLog)).all()
        assert len(rows)==1 and rows[0].tool_call_id=='cancel-backoff'
        assert rows[0].retry_count==0
