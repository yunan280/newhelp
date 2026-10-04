import copy
import json
from dataclasses import replace
from importlib import import_module

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from mewhelp.ch07.budget import ContextBudget
from mewhelp.ch07.config import BudgetProfile
from mewhelp.ch07.types import ConversationSnapshot, HistoryTurn, LedgerMessage


def projection():
    try:
        return import_module('mewhelp.ch07.projection')
    except ModuleNotFoundError:
        pytest.fail('Ch07 must project whole turns without changing full history')


def fixtures():
    first = HistoryTurn('first', 10, 14, (
        HumanMessage('订单1001物流如何', id='u1'),
        AIMessage('', id='call', tool_calls=[{'id': 'c1', 'name': 'query_order',
                    'args': {'order_id': '1001'}, 'type': 'tool_call'}]),
        ToolMessage(json.dumps({'status': 'signed', 'order_id': '1001', 'detail': '原' * 700},
                               ensure_ascii=False), tool_call_id='c1', name='query_order'),
        AIMessage('答' * 150, id='a1')))
    second = HistoryTurn('second', 23, 29, (HumanMessage('继续', id='u2'),
                                            AIMessage('已答', id='a2')))
    rows = (LedgerMessage(10, 'user', '订单1001物流如何'), LedgerMessage(14, 'assistant', '答' * 150),
            LedgerMessage(23, 'user', '继续'), LedgerMessage(29, 'assistant', '已答'))
    snapshot = ConversationSnapshot(1, 'one', 'alice', 0, 0, rows, ())
    budget = ContextBudget(0, 0, 10000, 10000, 7000, 3000, {}, BudgetProfile().fingerprint)
    return (first, second), snapshot, budget


def test_recent_messages_remain_byte_for_byte():
    module = projection()
    turns, snapshot, budget = fixtures()
    before = copy.deepcopy(turns)
    history = module.project_history(turns, snapshot, budget, profile=BudgetProfile())
    assert history.layer1 == (*turns[0].messages, *turns[1].messages)
    assert turns == before and history.layer2 == ()


def test_layer2_preserves_user_and_shortens_assistant():
    module = projection()
    turns, snapshot, budget = fixtures()
    snapshot = replace(snapshot, layer1_from_msg_id=14)
    history = module.project_history(turns, snapshot, budget, profile=BudgetProfile())
    assert history.layer2[0].content == '订单1001物流如何'
    assert history.layer2[-1].content == '答' * 60 + '…[已截短]'
    assert history.layer2[2].content == '[工具结果 name=query_order call_id=c1 object=1001 status=signed]'
    assert history.layer2[1].tool_calls[0]['id'] == history.layer2[2].tool_call_id


def test_tools_count_without_ledger_rows_and_degrade_whole_turn():
    module = projection()
    turns, snapshot, budget = fixtures()
    history = module.project_history(turns, snapshot, replace(budget, layer1=200),
                                     profile=BudgetProfile())
    assert history.layer1_from_msg_id == 14
    assert history.layer1 == turns[1].messages
    assert history.tokens['layer2_projected'] < 300
    assert len(snapshot.messages) == 4


def test_large_single_turn_degrades_whole():
    module = projection()
    turns, snapshot, budget = fixtures()
    history = module.project_history(turns[:1], snapshot, replace(budget, layer1=200),
                                     profile=BudgetProfile())
    assert history.layer1 == () and history.layer1_from_msg_id == 14
    assert len(history.layer2) == 4


def test_snapshot_boundaries_are_inclusive():
    module = projection()
    turns, snapshot, budget = fixtures()
    history = module.project_history(turns, replace(snapshot, summary_upto_msg_id=14,
        layer1_from_msg_id=29), budget, profile=BudgetProfile())
    assert history.layer1 == () and history.layer2 == turns[1].messages


def test_uncommitted_failed_turn_excluded():
    module = projection()
    turns, snapshot, _ = fixtures()
    tagged = []
    for turn in turns:
        for message in turn.messages:
            tagged.append(message.model_copy(update={'additional_kwargs': {'ch07': {
                'turn_id': turn.turn_id, 'from_msg_id': turn.from_msg_id,
                'upto_msg_id': turn.upto_msg_id, 'committed': True}}}))
    tagged.append(HumanMessage('失败的一轮', additional_kwargs={'ch07': {
        'turn_id': 'failed', 'committed': False}}))
    result = module.group_committed_turns(tagged, snapshot, current_turn_id='failed')
    assert [t.turn_id for t in result] == ['first', 'second']
    assert len(result[0].messages) == 4


def test_legacy_ambiguous_checkpoint_falls_back_to_visible_mysql():
    module = projection()
    _, snapshot, _ = fixtures()
    result = module.group_committed_turns([HumanMessage('无法映射')], snapshot,
                                         current_turn_id='current')
    assert [t.from_msg_id for t in result] == [10, 23]
    assert [len(t.messages) for t in result] == [2, 2]
