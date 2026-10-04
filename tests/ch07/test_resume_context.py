from importlib import import_module

import pytest

from tests.ch06.conftest import open_selection_runtime
from tests.ch06.test_selection import request, waiting


async def test_preparing_new_history_keeps_native_interrupt_and_resume(session_factory, tmp_path):
    try:
        module = import_module('mewhelp.ch07.context')
    except ModuleNotFoundError:
        pytest.fail('Ch07 must refresh context without updating an interrupted checkpoint')
    from mewhelp.ch06.selection import active_selection, resume_order
    async with open_selection_runtime(session_factory, tmp_path / 'resume.sqlite') as runtime:
        before = await waiting(runtime, 'refresh')
        config = {'configurable': {'thread_id': 'refresh'}}
        snapshot = await runtime.graph.aget_state(config)
        ctx = await module.prepare_request_context(runtime.context, snapshot.values)
        after = await runtime.graph.aget_state(config)
        assert after.next == snapshot.next and active_selection(after) == active_selection(snapshot)
        assert ctx is not runtime.context
        result = await resume_order(runtime, request(before))
        assert result.order.order_id == '1001' and result.status == 'completed'
