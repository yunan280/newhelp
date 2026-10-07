import json
from dataclasses import replace

from langchain_core.messages import AIMessage

from mewhelp.ch05.schemas import TurnRequest
from mewhelp.ch05.service import run_turn


async def attach_tools(runtime, tmp_path, session_factory):
    from mewhelp.ch08.config import ToolSystemSettings
    from mewhelp.ch08.runtime import Ch08ToolRuntime
    from mewhelp.tools.audit import ToolAuditWriter
    from mewhelp.tools.engine import ToolExecutionEngine
    from mewhelp.tools.registry import ToolRegistry
    path = tmp_path / 'tools.json'
    path.write_text('{"servers":{},"permissions":{}}')
    directory = tmp_path / 'plugins'
    directory.mkdir(exist_ok=True)
    engine = ToolExecutionEngine(ToolAuditWriter(session_factory))
    tools = Ch08ToolRuntime(ToolRegistry(), engine, ToolSystemSettings(path, directory), session_factory)
    runtime.context = replace(runtime.context, tool_runtime=tools)
    return directory


async def test_registered_plugin_reaches_agent_in_real_graph(workflow_runtime, tmp_path, session_factory, model_factory):
    directory = await attach_tools(workflow_runtime, tmp_path, session_factory)
    (directory / 'hello.py').write_text('''from langchain_core.tools import tool
from mewhelp.tools.registry import ToolSpec
@tool
def say_hello(name: str) -> str:
    """查询用户专属问候语。"""
    return "你好，" + name
def register(registry):
    registry.register(ToolSpec(say_hello))
''', encoding='utf-8')
    class Classifier:
        async def ainvoke(self, messages):
            return AIMessage(content=json.dumps({'intent': '其他', 'confidence': .99, 'matched_tool': 'say_hello'}))
    workflow_runtime.context = replace(workflow_runtime.context, router_model_factory=lambda **kw: Classifier())
    model_factory.decisions = [AIMessage('', tool_calls=[{'name': 'say_hello', 'args': {'name': '小明'}, 'id': 'hello1', 'type': 'tool_call'}]), AIMessage(content='{"reply_mode":"answer","suggested_actions":[],"ticket_type":null}')]
    result = await run_turn(workflow_runtime, TurnRequest(message='请查询小明的专属问候语', session_id='plugin-chat'))
    assert result.route == 'business'
    assert result.tool_trace[0].content == '你好，小明'
    state = (await workflow_runtime.graph.aget_state({'configurable': {'thread_id': result.session_id}})).values
    assert state['tool_catalog'][0]['name'] == 'say_hello'
    assert state['tool_catalog_hash']
    assert all('ToolSpec' not in str(v) for v in state['tool_catalog'])
