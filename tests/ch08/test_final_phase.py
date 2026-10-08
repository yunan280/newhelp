import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from mewhelp.ch05.agent import prompt_messages
from mewhelp.ch08.evaluation import grade_case


def test_answer_mode_is_supplied_after_tool_feedback(monkeypatch):
    monkeypatch.setenv('MODEL_CONTEXT_WINDOW', '32768')
    call = {'name':'query_order', 'args':{'order_id':'1001'}, 'id':'c1', 'type':'tool_call'}
    state = {'question':'查订单', 'messages':[], 'evidence':None,
             'agent_messages':[AIMessage('',tool_calls=[call]), ToolMessage('参数不合法',tool_call_id='c1',status='error')],
             'decision':{'reply_mode':'clarify','suggested_actions':[],'ticket_type':None}}
    messages = prompt_messages(state, phase='answer')
    assert isinstance(messages[-1], HumanMessage)
    assert json.loads(messages[-1].content)['phase'] == 'answer'
    assert messages[-2].tool_call_id == 'c1'


def test_evaluation_rejects_control_json_and_pseudo_tool_calls_as_user_answers():
    case = {'question':'查询失败', 'observation':'工具错误', 'expected':{'forbidden':'已成功'}}
    for answer in ('{"reply_mode":"clarify"}', '<｜｜DSML｜｜ calls>query_order'):
        assert grade_case(case, {'answer':answer})
