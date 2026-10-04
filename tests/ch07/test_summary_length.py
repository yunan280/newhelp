import pytest
from langchain_core.messages import HumanMessage

from mewhelp.ch07.summary_model import validate_summary
from mewhelp.ch07.types import SummaryResult


@pytest.mark.parametrize('length', [30, 46, 49, 200])
def test_approved_summary_length_range(length):
    content = '订单1001查物流，尚未解决。' + '待' * (length - 15)
    assert len(content) == length
    validate_summary(SummaryResult(content, {}, 0), [HumanMessage('订单1001查物流，尚未解决。')])


@pytest.mark.parametrize('length', [29, 201])
def test_summary_length_rejects_outside_approved_range(length):
    with pytest.raises(ValueError, match='summary length'):
        validate_summary(SummaryResult('待' * length, {}, 0), [HumanMessage('查询情况待确认')])
