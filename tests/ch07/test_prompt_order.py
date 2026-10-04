from importlib import import_module

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from mewhelp.ch07.budget import ContextBudget
from mewhelp.ch07.config import BudgetProfile
from mewhelp.ch07.types import HistoryContext


def test_prompt_order_current_once_static_prefix_and_complete_react():
    try:
        module = import_module('mewhelp.ch07.projection')
    except ModuleNotFoundError:
        pytest.fail('Ch07 must render stable system then chronological history and background')
    budget = ContextBudget(0, 0, 10000, 10000, 7000, 3000, {}, BudgetProfile().fingerprint)
    history = HistoryContext(1, 4, 8, '最早订单1001待查询', (),
        (HumanMessage('中间'), AIMessage('中间答')), (HumanMessage('最近'), AIMessage('最近答')),
        (), {}, budget)
    call = AIMessage('', tool_calls=[{'id': 'now', 'name': 'query_order',
                                     'args': {}, 'type': 'tool_call'}])
    result = module.model_messages(history, system='固定人设与红线', question='原话',
        background={'evidence': '证据一'}, current_react=(call, ToolMessage('结果', tool_call_id='now')))
    assert [m.type for m in result] == ['system', 'human', 'ai', 'human', 'ai',
                                         'human', 'human', 'ai', 'tool']
    assert sum(m.content == '原话' for m in result) == 1
    assert '最早订单1001' in result[6].content and '证据一' in result[6].content
    changed = module.model_messages(history, system='固定人设与红线', question='另一句',
                                    background={'evidence': '不同证据'})
    assert result[0] == changed[0]
    assert sum(isinstance(m, SystemMessage) for m in result) == 1
