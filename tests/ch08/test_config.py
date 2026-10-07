import json

import pytest


def test_config_rejects_partial_or_untrusted_permissions(tmp_path):
    from mewhelp.ch08.config import read_tool_config
    path = tmp_path / 'tools.json'
    path.write_text('{', encoding='utf-8')
    with pytest.raises(ValueError):
        read_tool_config(path)
    path.write_text(json.dumps({'servers': {'x': {'url': 'http://localhost/mcp', 'transport': 'stdio'}}, 'permissions': {}}))
    with pytest.raises(ValueError):
        read_tool_config(path)
