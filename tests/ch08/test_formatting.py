import json

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

from mewhelp.tools.contracts import ToolSpec


@tool
def query() -> dict:
    """查询物流。"""
    return {}


def test_mcp_artifact_projection_and_enum_translation():
    from mewhelp.tools.formatting import format_result
    spec = ToolSpec(query, source='mcp', mcp_server='logistics', result_fields=('status', 'tracking'), enum_labels={'SIGNED': '已签收'})
    raw = ToolMessage(content=[{'type': 'text', 'text': 'raw internal data'}], tool_call_id='1', artifact={'structured_content': {'outcome': 'success', 'data': {'status': 'SIGNED', 'tracking': '上海', 'internal': 'secret'}}})
    ok, content, error, _artifact = format_result(spec, raw)
    assert ok and error is None
    assert json.loads(content) == {'status': '已签收', 'tracking': '上海'}
    assert '\\u' not in content


def test_business_empty_and_mcp_error_are_failures():
    from mewhelp.tools.formatting import format_result
    assert not format_result(ToolSpec(query), {'outcome': 'not_found', 'data': None})[0]
    assert not format_result(ToolSpec(query), ToolMessage(content='坏消息', status='error', tool_call_id='1'))[0]


def test_long_chinese_result_truncated():
    from mewhelp.tools.formatting import format_result
    result = format_result(ToolSpec(query), '中文' * 2000)
    assert '截断' in result[1]
    assert len(result[1]) < 2100
