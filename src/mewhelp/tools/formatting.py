"""投影业务回答所需字段，中文 JSON 和内部码由本地规则生成。"""
import json

from langchain_core.messages import ToolMessage

from .contracts import ToolSpec

TOOL_RESULT_MAX_CHARS = 2000


def as_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def truncate(text: str) -> str:
    if len(text) <= TOOL_RESULT_MAX_CHARS:
        return text
    return f'{text[:TOOL_RESULT_MAX_CHARS]}…(已截断,原始返回共 {len(text)} 字符)'


def format_result(spec: ToolSpec, raw: object) -> tuple[bool, str, str | None, object | None]:
    artifact = raw.artifact if isinstance(raw, ToolMessage) else None
    value = raw.content if isinstance(raw, ToolMessage) else raw
    if isinstance(raw, ToolMessage) and raw.status == 'error':
        return False, truncate(as_text(value)), 'tool_error', artifact
    if spec.source == 'mcp':
        if isinstance(artifact, dict):
            value = artifact.get('structured_content', artifact.get('structuredContent', value))
        if isinstance(value, list):
            text = '\n'.join(b.get('text', '') for b in value if isinstance(b, dict) and b.get('type') == 'text')
            try:
                value = json.loads(text)
            except (ValueError, TypeError):
                value = text or value
    if isinstance(value, dict) and value.get('outcome') in ('not_found', 'error'):
        return False, as_text(value.get('message') or '没有查到对应业务记录。'), value['outcome'], artifact
    if isinstance(value, dict) and value.get('outcome') == 'success':
        value = value.get('data')
    if isinstance(value, dict) and spec.result_fields is not None:
        value = {k: v for k, v in value.items() if k in spec.result_fields}
    def translate(item):
        if isinstance(item, str):
            return (spec.enum_labels or {}).get(item, item)
        if isinstance(item, dict):
            return {k: translate(v) for k, v in item.items()}
        if isinstance(item, list):
            return [translate(v) for v in item]
        return item
    content = as_text(translate(value))
    if value is None or value == '' or value == [] or value == {}:
        return False, '没有查到对应业务记录。', 'not_found', artifact
    return True, content if spec.preserve_raw or artifact is not None and spec.source == 'builtin' else truncate(content), None, artifact
