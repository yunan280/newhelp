from importlib import import_module

import pytest
from langchain_core.messages import AIMessage, HumanMessage


def modules():
    try:
        return import_module('mewhelp.ch07.config'), import_module('mewhelp.ch07.tokens')
    except ModuleNotFoundError:
        pytest.fail('Ch07 needs one counter for Chinese, tool schemas and requests')


def test_chinese_and_serialized_tools_share_counter():
    config, tokens = modules()
    profile = config.BudgetProfile()
    chinese = [HumanMessage('中' * 100)]
    english = [HumanMessage('a' * 100)]
    assert tokens.estimate_messages(chinese, profile=profile) >= 100
    assert tokens.estimate_messages(chinese, profile=profile) > tokens.estimate_messages(
        english, profile=profile) + 65
    tool = {'type': 'function', 'function': {'name': '订单', 'description': '中' * 500}}
    assert tokens.estimate_request(chinese, [tool], profile=profile) > 600
    call = AIMessage('', tool_calls=[{'name': '订单', 'id': 'c1',
                                     'args': {'订单号': '1001'}, 'type': 'tool_call'}])
    assert tokens.estimate_messages([call], profile=profile) > tokens.estimate_messages(
        [AIMessage('')], profile=profile)


def test_checkpoint_metadata_is_not_counted_on_wire():
    config, tokens = modules()
    profile = config.BudgetProfile()
    raw = HumanMessage('你好', id='stable')
    tagged = raw.model_copy(update={'additional_kwargs': {'ch07': {'debug': '中' * 10000}}})
    assert tokens.estimate_messages([raw], profile=profile) == tokens.estimate_messages(
        [tagged], profile=profile)
