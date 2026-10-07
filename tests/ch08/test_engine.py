import asyncio
import time
from dataclasses import replace

import pytest
from langchain_core.tools import tool
from sqlalchemy import select

from mewhelp.tools.contracts import ToolCallContext, ToolSpec, WriteAuthorization
from mewhelp.tools.registry import ToolRegistry


@pytest.mark.asyncio
@pytest.mark.parametrize('failure,attempts,status', [('timeout', 3, '超时'), ('value', 1, '失败'), ('empty', 1, '失败'), ('network', 3, '成功')])
async def test_engine_failure_triage_and_one_audit(audit_factory, failure, attempts, status):
    from mewhelp.db.models import ToolAuditLog
    from mewhelp.tools.audit import ToolAuditWriter
    from mewhelp.tools.engine import ToolExecutionEngine
    calls = []
    @tool
    async def query(limit: int) -> dict:
        """查询示例业务。"""
        calls.append(limit)
        if failure == 'timeout':
            await asyncio.sleep(.08)
        if failure == 'value':
            raise ValueError('程序故障')
        if failure == 'network' and len(calls) < 3:
            raise ConnectionResetError('连接重置')
        return {'outcome': 'not_found' if failure == 'empty' else 'success', 'data': None if failure == 'empty' else {'count': 2}}
    async def no_sleep(_):
        return
    engine = ToolExecutionEngine(ToolAuditWriter(audit_factory), sleep=no_sleep)
    registry = ToolRegistry({'query': ToolSpec(query, timeout_seconds=.02 if failure == 'timeout' else 1)}, engine=engine)
    result = await registry.run('query', {'limit': 2}, context=ToolCallContext(conversation_id=44, tool_call_id='query1'))
    assert (result.attempts, result.status, result.retry_count) == (attempts, status, attempts - 1)
    with audit_factory() as db:
        row = db.scalars(select(ToolAuditLog)).all()
        assert len(row) == 1
        assert row[0].retry_count == attempts - 1


@pytest.mark.asyncio
async def test_validation_and_denials_never_invoke(audit_factory):
    from mewhelp.tools.audit import ToolAuditWriter
    from mewhelp.tools.engine import ToolExecutionEngine
    calls = []
    @tool
    def create_ticket(description: str, ticket_type: str) -> str:
        """创建工单。"""
        calls.append(description)
        return 'ticket1'
    registry = ToolRegistry({'create_ticket': ToolSpec(create_ticket, permission='write')}, engine=ToolExecutionEngine(ToolAuditWriter(audit_factory)))
    result = await registry.run('create_ticket', {'description': '键盘坏了', 'ticket_type': '售后', 'confirmed': True})
    assert result.status == '校验拦下'
    result = await registry.run('create_ticket', {'description': '键盘坏了', 'ticket_type': '售后'})
    assert (result.status, result.attempts, result.retry_count) == ('权限拒绝', 0, 0)
    assert not calls


@pytest.mark.asyncio
async def test_confirmed_write_timeout_is_not_retried(audit_factory):
    from mewhelp.tools.audit import ToolAuditWriter
    from mewhelp.tools.engine import ToolExecutionEngine
    from mewhelp.tools.permissions import arguments_hash
    calls = []
    @tool
    def create_ticket(description: str, ticket_type: str) -> str:
        """创建工单。"""
        calls.append(1)
        time.sleep(.04)
        return 'ticket1'
    args = {'description': '键盘坏了', 'ticket_type': '售后'}
    auth = WriteAuthorization('preview', 'confirm1', 9, 'session1', 'user1', 'create_ticket', arguments_hash(args))
    context = ToolCallContext(9, 'session1', 'user1', authorization=auth)
    engine = ToolExecutionEngine(ToolAuditWriter(audit_factory))
    snapshot = ToolRegistry({'create_ticket': ToolSpec(create_ticket, permission='write', timeout_seconds=.005)}).snapshot()
    result = await engine.execute(snapshot, 'create_ticket', args, context)
    assert (result.status, result.attempts, result.retry_count) == ('超时', 1, 0)
    assert '可能' in result.content
    await asyncio.sleep(.05)
    assert len(calls) == 1
    invalid = await engine.execute(snapshot, 'create_ticket', args, replace(context, user_id='other'))
    assert invalid.status == '权限拒绝'


@pytest.mark.asyncio
async def test_deadline_exhausted_before_backoff(audit_factory):
    from mewhelp.tools.audit import ToolAuditWriter
    from mewhelp.tools.engine import ToolExecutionEngine
    @tool
    async def query() -> str:
        """示例查询。"""
        raise TimeoutError('临时故障')
    result = await ToolExecutionEngine(ToolAuditWriter(audit_factory)).execute(ToolRegistry({'query': ToolSpec(query)}).snapshot(), 'query', {}, ToolCallContext(deadline_monotonic=time.monotonic() + .03))
    assert result.attempts == 1
    assert result.status == '超时'
