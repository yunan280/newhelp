import json
from importlib import import_module
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from mewhelp.ch05.agent import final_messages, prompt_messages
from mewhelp.ch07.budget import compute_budget
from mewhelp.ch07.config import BudgetProfile, ContextSettings
from mewhelp.ch07.context import history_payload
from mewhelp.ch07.types import HistoryContext


def history():
    profile = BudgetProfile()
    return HistoryContext(1, 0, 0, '', (), (), (HumanMessage('旧问题'), AIMessage('旧原文答复')),
                          (), {}, compute_budget(ContextSettings(_env_file=None), profile))


def test_variable_background_never_becomes_system_or_duplicates_current_question():
    state = {'question': '原话', 'resolved_question': '消解后的原话',
             'history_ctx': history_payload(history()), 'messages': [], 'agent_messages': [],
             'evidence': {'sources': []}, 'decision': {'reply_mode': 'answer'}}
    decide = prompt_messages(state)
    answer = final_messages(state)
    assert sum(isinstance(m, SystemMessage) for m in decide) == 1
    assert sum(isinstance(m, SystemMessage) for m in answer) == 1
    assert decide[0].content == answer[0].content
    assert [m.content for m in decide[1:3]] == ['旧问题', '旧原文答复']
    assert sum(m.content == '原话' for m in decide) == 1
    assert json.loads(decide[-1].content)['resolved_question'] == '消解后的原话'


async def test_model_ctx_matches_actual_chatopenai_payload_including_tool_pairs(monkeypatch):
    try:
        module = import_module('mewhelp.ch07.observability')
    except ModuleNotFoundError:
        pytest.fail('Ch07 must log the messages/tools actually sent by ChatOpenAI')
    from mewhelp import llm
    monkeypatch.setattr(llm, 'get_settings', lambda: SimpleNamespace(llm_model='test-model',
        openai_base_url='https://test.invalid/v1', openai_api_key='test-key', llm_temperature=0))
    captured = []
    def handle(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'id': 'r', 'object': 'chat.completion', 'created': 1,
            'model': 'test-model', 'choices': [{'index': 0, 'message': {'role': 'assistant',
            'content': '回答'}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 30, 'completion_tokens': 2, 'total_tokens': 32}})
    messages = [SystemMessage('固定规则'), HumanMessage('订单1001'), AIMessage('',
        tool_calls=[{'id': 'c1', 'name': 'query_order', 'args': {'order_id': '1001'}, 'type': 'tool_call'}]),
        ToolMessage('订单1001，原始结果\n下一行', name='query_order', tool_call_id='c1')]
    tools = [{'type': 'function', 'function': {'name': 'query_order', 'description': '查询订单',
              'parameters': {'type': 'object', 'properties': {'order_id': {'type': 'string'}}}}}]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        model = llm.get_chat_model(http_async_client=client, use_responses_api=False).bind_tools(tools)
        logged = module.log_model(messages, tools, state={'turn_id': 't'}, purpose='decision',
                                   model_name='test-model', profile=BudgetProfile())
        await model.ainvoke(messages)
    assert logged['messages'] == captured[0]['messages']
    assert logged['tools'] == captured[0]['tools']
