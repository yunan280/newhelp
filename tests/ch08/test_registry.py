import asyncio

import pytest
from langchain_core.tools import tool

from mewhelp.tools.registry import ToolRegistry, ToolSpec


@tool
def echo(text: str) -> str:
    """返回给定文字。"""
    return text


def test_register_appears_without_rebuilding_registry():
    registry = ToolRegistry({})
    registry.register(ToolSpec(echo))
    assert registry.snapshot().get('echo').input_schema['type'] == 'object'
    assert registry.tools()[0].name == 'echo'


def test_snapshot_stays_fixed_after_registration():
    registry = ToolRegistry({})
    before = registry.snapshot()
    registry.register(ToolSpec(echo))
    assert before.get('echo') is None
    assert before.fingerprint != registry.snapshot().fingerprint
    with pytest.raises(TypeError):
        before.specs['echo'] = ToolSpec(echo)


def test_duplicate_cannot_override_create_ticket():
    registry = ToolRegistry({'echo': ToolSpec(echo)})
    with pytest.raises(ValueError):
        registry.register(ToolSpec(echo))
    ticket = echo.model_copy(update={'name': 'create_ticket'})
    registry.register(ToolSpec(ticket, permission='write'))
    with pytest.raises(ValueError):
        registry.replace_source('plugin:bad', [ToolSpec(ticket)])


@pytest.mark.asyncio
async def test_concurrent_refresh_keeps_complete_catalog():
    registry = ToolRegistry({})
    async def refresh():
        registry.replace_source('plugin:echo', [ToolSpec(echo)])
        assert registry.snapshot().get('echo')
    await asyncio.gather(*(refresh() for _ in range(10)))
    assert registry.names() == ['echo']
