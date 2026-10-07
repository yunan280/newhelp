"""运维本地配置；热加载失败关闭外部授权，不采信 Server hints。"""
import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


@dataclass(frozen=True)
class ToolSystemSettings:
    config_path: Path = Path('config/ch08-tools.json')
    plugin_dir: Path = Path('tool_plugins')


def read_tool_config(path: Path) -> dict:
    try:
        config = json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError) as exc:
        raise ValueError('工具配置读取失败，外部权限关闭') from exc
    if not isinstance(config, dict) or not isinstance(config.get('servers'), dict) or not isinstance(config.get('permissions'), dict):
        raise ValueError('工具配置必须含 servers/permissions 对象')
    connections = {}
    for name, connection in config['servers'].items():
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', name) or not isinstance(connection, dict):
            raise ValueError('Server 名称或连接不合法')
        url = urlparse(connection.get('url', ''))
        if url.scheme not in ('http', 'https') or not url.hostname or connection.get('transport', 'streamable_http') != 'streamable_http':
            raise ValueError('仅支持 Streamable HTTP Server')
        connections[name] = {'url': connection['url'], 'transport': 'streamable_http'}
    for server, rules in config['permissions'].items():
        if server not in connections or not isinstance(rules, dict):
            raise ValueError('权限必须绑定已配置 Server')
        for name, rule in rules.items():
            if not isinstance(name, str) or not isinstance(rule, dict) or rule.get('mode') not in ('readonly', 'write', 'deny'):
                raise ValueError('工具本地权限不合法')
            if 'result_fields' in rule and (not isinstance(rule['result_fields'], list) or not all(isinstance(f, str) for f in rule['result_fields'])):
                raise ValueError('结果字段必须是字符串列表')
            if 'enum_labels' in rule and (not isinstance(rule['enum_labels'], dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in rule['enum_labels'].items())):
                raise ValueError('枚举映射必须为字符串字典')
    for field in ('discovery_timeout_seconds', 'execution_timeout_seconds'):
        value = config.get(field, 3.0)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 120:
            raise ValueError('工具超时配置不合法')
    return {**config, 'servers': connections}
