from importlib import import_module

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from mewhelp.ch07.types import SummaryResult


def modules():
    try:
        return import_module('mewhelp.ch07.evaluation'), import_module('mewhelp.ch07.summary_model')
    except ModuleNotFoundError:
        pytest.fail('Ch07 evaluations must freeze labels and reject invented numeric facts')


def test_freeze_never_accepts_changed_labels(tmp_path):
    evaluation, _ = modules()
    path = tmp_path / 'summary-calibration.jsonl'
    path.write_text('{"id":"c1","split":"calibration","expected":{"empty":true}}\n')
    evaluation.freeze_dataset(tmp_path)
    path.write_text('{"id":"c1","split":"calibration","expected":{"empty":false}}\n')
    with pytest.raises(ValueError, match='frozen'):
        evaluation.freeze_dataset(tmp_path)


def test_invented_order_or_phone_is_rejected():
    _, model = modules()
    with pytest.raises(ValueError, match='numeric'):
        model.validate_summary(SummaryResult('用户询问订单9999物流。' * 5, {}, 1),
                                [HumanMessage('订单1001查物流')])


def test_required_phone_cannot_disappear():
    _, model = modules()
    with pytest.raises(ValueError, match='identifier'):
        model.validate_summary(SummaryResult('用户询问订单1001的物流进展，尚未得到查询结果。' * 3, {}, 1),
                                [HumanMessage('订单1001，电话13800138000，查物流')])


def test_empty_business_summary_is_valid():
    _, model = modules()
    model.validate_summary(SummaryResult('本段无需要保留的业务事实。', {}, 1),
                            [HumanMessage('你好，谢谢')])


async def test_short_candidate_retries_original_batch_once_and_counts_both_requests():
    _, module = modules()
    from mewhelp.ch07.config import BudgetProfile, ContextSettings
    responses = [AIMessage('用户问订单1001物流，仍未解决。', usage_metadata={
        'input_tokens': 80, 'output_tokens': 10, 'total_tokens': 90}),
        AIMessage('用户询问订单1001的物流进展。客服本批尚未查询到物流结果，'
                  '用户提出的查询诉求仍未解决，目前没有可确认的派送结论。',
                  usage_metadata={'input_tokens': 100, 'output_tokens': 40, 'total_tokens': 140})]
    requests = []
    class Upstream:
        async def ainvoke(self, messages):
            requests.append(messages)
            return responses.pop(0)
    model = module.ChatSummaryModel(ContextSettings(_env_file=None), BudgetProfile(),
                                    model_factory=lambda *a, **k: Upstream())
    result = await model.summarize(batch=[HumanMessage('订单1001查物流，未解决')], background='')
    assert len(result.content) >= 50 and result.usage['input_tokens'] == 180
    assert len(requests) == 2
    assert '用户问订单1001物流，仍未解决。' not in str(requests[1])
