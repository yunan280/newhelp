import pytest
from langchain_core.messages import HumanMessage

from mewhelp.ch05.runtime import open_runtime
from mewhelp.ch05.schemas import TurnRequest
from mewhelp.ch05.service import run_turn
from mewhelp.ch07.config import ContextSettings


async def test_context_settings_bind_real_tool_steps_and_output_limit(
        session_factory, checkpoint_settings, model_factory, router_settings):
    settings = ContextSettings(model_context_window=18000, max_output_tokens=2000,
        max_user_input_tokens=2000, max_agent_steps=3, tool_result_max_tokens=1200,
        rerank_top_k=5, _env_file=None)
    async with open_runtime(session_factory, settings=checkpoint_settings,
        model_factory=model_factory, router_settings=router_settings,
        router_model_factory=model_factory.router, context_settings=settings) as runtime:
        assert (runtime.context.limits.max_tools, runtime.context.limits.max_decisions,
                runtime.context.limits.final_max_tokens) == (3, 5, 2000)


async def test_oversized_chinese_input_is_explicit_and_sends_no_model(workflow_runtime, model_factory):
    workflow_runtime.context.settings = ContextSettings(max_user_input_tokens=80, _env_file=None)
    result = await run_turn(workflow_runtime, TurnRequest(message='中' * 200, session_id='large-input'))
    assert result.stop_reason == 'input_limit' and '输入' in result.answer
    assert not model_factory.requests


async def test_real_request_preflights_remaining_peak_before_sending_model():
    from tests.ch05.test_agent import ScriptedModel, context, decision, initial
    from mewhelp.ch05.agent import decide_agent
    model = ScriptedModel([decision()])
    ctx = context(model)
    ctx.settings = ContextSettings(model_context_window=18000, max_output_tokens=2000,
        max_user_input_tokens=2000, max_agent_steps=3, tool_result_max_tokens=1200,
        rerank_top_k=5, _env_file=None)
    state = initial(messages=[HumanMessage('中' * 15000)])
    update = await decide_agent(state, ctx)
    assert update['stop_reason'] == 'context_budget' and not model.requests


async def test_three_tool_budget_rejects_entire_parallel_batch(workflow_runtime, model_factory):
    from langchain_core.messages import AIMessage
    workflow_runtime.context.limits = __import__('dataclasses').replace(
        workflow_runtime.context.limits, max_tools=3)
    model_factory.intents = ['订单']
    model_factory.decisions = [AIMessage('', tool_calls=[{'id': f'c{i}', 'name': 'query_order',
        'args': {'order_id': str(1001 + i)}, 'type': 'tool_call'} for i in range(4)])]
    result = await run_turn(workflow_runtime, TurnRequest(message='查询几个订单', session_id='batch-limit'))
    assert result.stop_reason == 'tool_limit' and result.tool_trace == []


async def test_tool_overflow_keeps_full_raw_but_prevents_next_model(monkeypatch):
    from langchain_core.tools import tool
    from tests.ch05.test_agent import ScriptedModel, context, initial, tool_message
    from mewhelp.ch05 import agent
    from mewhelp.tools.registry import ToolRegistry, ToolSpec
    @tool
    def query_order(order_id: str) -> str:
        """Return a full large order result."""
        return '原' * 3000 + '末尾事实'
    registry = ToolRegistry({'query_order': ToolSpec(query_order, preserve_raw=True)})
    monkeypatch.setattr(agent, 'build_read_registry', lambda: registry)
    model = ScriptedModel([tool_message()])
    ctx = context(model)
    ctx.settings = ContextSettings(tool_result_max_tokens=1200, _env_file=None)
    state = initial()
    state.update(await agent.decide_agent(state, ctx))
    update = await agent.execute_agent_tools(state, ctx, lambda e: None)
    state.update(update)
    assert update['stop_reason'] == 'tool_result_limit'
    assert update['messages'][0].content == '原' * 3000 + '末尾事实'
    assert update['tool_trace'][0]['content'].endswith('末尾事实')
    assert await agent.decide_agent(state, ctx) == {} and len(model.requests) == 1


async def test_repair_payload_also_preflighted(monkeypatch):
    from langchain_core.messages import AIMessage
    from tests.ch05.test_agent import ScriptedModel, context, initial
    from mewhelp.ch05 import agent
    model = ScriptedModel([AIMessage('invalid')])
    ctx = context(model)
    ctx.settings = ContextSettings(model_context_window=18000, _env_file=None)
    original = agent.prompt_messages
    def messages(state, *, phase='decide', correction=None):
        result = original(state, phase=phase, correction=correction)
        return result + [HumanMessage('中' * 18000)] if phase == 'repair' else result
    monkeypatch.setattr(agent, 'prompt_messages', messages)
    update = await agent.decide_agent(initial(), ctx)
    assert update['stop_reason'] == 'context_budget' and len(model.requests) == 1


async def test_startup_measures_actual_prefix_before_installing_runtime(
        monkeypatch, session_factory, checkpoint_settings, model_factory):
    from mewhelp.ch05 import prompts
    from mewhelp.ch07.budget import ContextBudgetError
    monkeypatch.setattr(prompts, 'MAIN_SYSTEM', '中' * 18000)
    settings = ContextSettings(model_context_window=18000, max_output_tokens=2000,
        max_user_input_tokens=2000, max_agent_steps=3, tool_result_max_tokens=1200,
        rerank_top_k=5, _env_file=None)
    with pytest.raises(ContextBudgetError):
        async with open_runtime(session_factory, settings=checkpoint_settings,
            model_factory=model_factory, context_settings=settings):
            pytest.fail('an impossible runtime was installed')


async def test_large_history_uses_model_window_not_reranker_evidence_ceiling(
        workflow_runtime, strong_evidence, session_factory):
    from mewhelp.ch05.workflow import gate_node
    from tests.ch05.test_agent import initial
    from mewhelp.db.models import Conversation
    with session_factory.begin() as session:
        session.add(Conversation(id=1, session_id='evidence-ceiling', user_id='alice'))
    state = initial(conversation_id=1, route='knowledge', user_id='alice',
        entry_point='chat_stream', messages=[HumanMessage('中' * 11000)],
        evidence=strong_evidence.model_dump())
    result = await gate_node(state, workflow_runtime.context, lambda e: None)
    assert result['gate']['passed'] and not result.get('refused')
