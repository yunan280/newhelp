import asyncio
import time

import pytest
from sqlalchemy import select

from mewhelp.tools.contracts import ToolCallContext, ToolResult


def result(**changes):
    values = {'name':'查物流', 'args':{'order_id':'中文'}, 'ok':False, 'content':'查询超时',
              'error':'x' * 900, 'elapsed_ms':750, 'attempts':3, 'status':'超时',
              'retry_count':2, 'source':'mcp', 'mcp_server':'logistics', 'tool_call_id':'call1'}
    values.update(changes)
    return ToolResult(**values)


@pytest.mark.asyncio
async def test_audit_accepts_orphan_conversation_id(audit_factory):
    from mewhelp.db.models import ToolAuditLog
    from mewhelp.tools.audit import ToolAuditWriter
    writer = ToolAuditWriter(audit_factory)
    await writer.record(result(), ToolCallContext(conversation_id=99999))
    with audit_factory() as db:
        row = db.scalar(select(ToolAuditLog))
        assert row.conversation_id == 99999
        assert row.arguments == {'order_id': '中文'}
        assert (row.status, row.retry_count, row.duration_ms) == ('超时', 2, 750)
        assert len(row.error_message) == 512
        assert row.mcp_server == 'logistics'


def test_audit_enum_values_match_supplied_ddl():
    from mewhelp.db.models import ToolAuditLog
    table = ToolAuditLog.__table__
    assert not table.foreign_keys
    assert table.c.status.type.enums == ['成功', '失败', '超时', '校验拦下', '权限拒绝']
    assert table.c.tool_source.type.enums == ['builtin', 'mcp']
    assert {i.name for i in table.indexes} == {'idx_conversation_id', 'idx_tool_name', 'idx_status'}


@pytest.mark.asyncio
async def test_replayed_terminal_call_does_not_add_audit(audit_factory):
    from mewhelp.db.models import ToolAuditLog
    from mewhelp.tools.audit import ToolAuditWriter
    writer = ToolAuditWriter(audit_factory)
    for _ in range(2):
        await writer.record(result(), ToolCallContext(conversation_id=9))
    with audit_factory() as db:
        assert len(db.scalars(select(ToolAuditLog)).all()) == 1


@pytest.mark.asyncio
async def test_audit_failure_does_not_change_tool_result():
    from mewhelp.tools.audit import ToolAuditWriter
    def broken():
        raise RuntimeError('db unavailable')
    original = result(ok=True, status='成功')
    await ToolAuditWriter(broken).record(original, ToolCallContext())
    assert original.ok


@pytest.mark.asyncio
async def test_audit_timeout_has_bounded_wait():
    from mewhelp.tools.audit import ToolAuditWriter
    def slow():
        time.sleep(.3)
        raise RuntimeError('unavailable')
    start = time.monotonic()
    await ToolAuditWriter(slow, timeout_seconds=.01).record(result(), ToolCallContext())
    assert time.monotonic() - start < .15
    await asyncio.sleep(.35)


@pytest.mark.asyncio
async def test_bad_json_or_oversized_error_is_safely_recorded(audit_factory):
    from mewhelp.db.models import ToolAuditLog
    from mewhelp.tools.audit import ToolAuditWriter
    await ToolAuditWriter(audit_factory).record(result(args={'value': object()}), ToolCallContext())
    with audit_factory() as db:
        assert isinstance(db.scalar(select(ToolAuditLog)).arguments['value'], str)


@pytest.mark.asyncio
async def test_stalled_audits_do_not_exhaust_business_tool_threads():
    from langchain_core.tools import tool

    from mewhelp.tools.audit import ToolAuditWriter
    from mewhelp.tools.engine import ToolExecutionEngine
    from mewhelp.tools.registry import ToolRegistry, ToolSpec
    def stalled():
        time.sleep(.3)
        raise RuntimeError('audit db stalled')
    writer = ToolAuditWriter(stalled, timeout_seconds=.005)
    await asyncio.gather(*(writer.record(result(tool_call_id=None), ToolCallContext()) for _ in range(70)))
    @tool
    def business_query() -> str:
        """普通同步业务查询。"""
        return '业务查询成功'
    engine = ToolExecutionEngine(writer)
    snapshot = ToolRegistry({'business_query': ToolSpec(business_query, retryable=False, timeout_seconds=.08)}).snapshot()
    observed = await engine.execute(snapshot, 'business_query', {}, ToolCallContext())
    assert observed.ok
    await asyncio.sleep(.7)


@pytest.mark.asyncio
async def test_healthy_concurrent_calls_each_have_an_audit(audit_factory):
    from mewhelp.db.models import ToolAuditLog
    from mewhelp.tools.audit import ToolAuditWriter
    writer = ToolAuditWriter(audit_factory)
    await asyncio.gather(*(writer.record(result(tool_call_id=f'parallel-{i}'),
                          ToolCallContext()) for i in range(20)))
    with audit_factory() as db:
        assert len(db.scalars(select(ToolAuditLog)).all()) == 20
    writer.close()
