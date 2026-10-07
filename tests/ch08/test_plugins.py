from mewhelp.tools.registry import ToolRegistry

PLUGIN = '''from langchain_core.tools import tool
from mewhelp.tools.registry import ToolSpec
@tool
def hello(text: str) -> str:
    """问候用户。"""
    return text
def register(registry):
    registry.register(ToolSpec(hello))
'''


def test_new_plugin_registration_and_removal(tmp_path):
    from mewhelp.tools.plugins import PluginLoader
    loader = PluginLoader(tmp_path)
    registry = ToolRegistry({})
    path = tmp_path / 'hello.py'
    path.write_text(PLUGIN, encoding='utf-8')
    loader.refresh(registry)
    assert registry.get('hello')
    assert loader.refresh(registry)['loaded'] == []
    path.unlink()
    loader.refresh(registry)
    assert not registry.get('hello')


def test_partial_plugin_load_does_not_mutate_snapshot(tmp_path):
    from mewhelp.tools.plugins import PluginLoader
    registry = ToolRegistry({})
    path = tmp_path / 'bad.py'
    path.write_text(PLUGIN.replace('registry.register(ToolSpec(hello))', "registry.register(ToolSpec(hello))\n    raise ValueError('partial')"), encoding='utf-8')
    report = PluginLoader(tmp_path).refresh(registry)
    assert report['errors']
    assert registry.names() == []
