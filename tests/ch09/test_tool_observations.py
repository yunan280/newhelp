import asyncio
import json

import pytest
from langchain_core.tools import tool

from mewhelp.tools.contracts import ToolCallContext, ToolSpec
from mewhelp.tools.engine import ToolExecutionEngine
from mewhelp.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_tool_denial_and_timeout_are_observed(ch09_module, span_client):
    runtime = ch09_module('observability').ObservationRuntime(client=span_client[0], public_key=span_client[2])
    context = ch09_module('contracts').RequestTraceContext

    @tool
    async def query(order_id: str) -> dict:
        """查询物流。"""
        await asyncio.sleep(.15)
        return {'outcome': 'success', 'data': {}}

    @tool
    async def write(description: str) -> str:
        """写外部数据。"""
        pytest.fail('permission denied tool must not run')

    async def no_sleep(_):
        pass

    engine = ToolExecutionEngine(sleep=no_sleep, observation_runtime=runtime)
    snapshot = ToolRegistry({'query': ToolSpec(query, timeout_seconds=.025),
                             'write': ToolSpec(write, permission='deny')}).snapshot()
    with runtime.request(context(session_id='s'), input={}):
        denial = await engine.prepare(snapshot, 'write', {'description': '测试'},
                                      ToolCallContext(tool_call_id='deny1'))
        timeout = await engine.execute(snapshot, 'query', {'order_id': '123'},
                                       ToolCallContext(tool_call_id='query1'))
    assert denial.result.status == '权限拒绝'
    assert (timeout.status, timeout.retry_count) == ('超时', 2)
    runtime.flush()
    spans = [s for s in span_client[1].get_finished_spans() if s.name.startswith('tool.')]
    assert len(spans) == 2
    output = {s.name: json.loads(s.attributes['langfuse.observation.output']) for s in spans}
    assert output['tool.write']['status'] == '权限拒绝'
    assert output['tool.query']['status'] == '超时'
    assert output['tool.query']['retry_count'] == 2
    assert output['tool.query']['elapsed_ms'] >= 40
    assert output['tool.query']['tool_call_id'] == 'query1'
    assert json.loads(spans[1].attributes['langfuse.observation.input'])['arguments'] == {
        'order_id': '123'}
