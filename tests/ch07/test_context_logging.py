from importlib import import_module

import pytest

from mewhelp.ch05.schemas import TurnRequest
from mewhelp.ch05.service import run_turn


async def test_history_ctx_is_logged_for_chitchat_without_main_agent(workflow_runtime, tmp_path):
    try:
        module = import_module('mewhelp.ch07.observability')
    except ModuleNotFoundError:
        pytest.fail('Ch07 must configure a UTF-8 app.log without extra CLI flags')
    path = tmp_path / 'app.log'
    module.configure_context_logging(path)
    await run_turn(workflow_runtime, TurnRequest(message='你好', session_id='logs'))
    await run_turn(workflow_runtime, TurnRequest(message='谢谢', session_id='logs'))
    text = path.read_text(encoding='utf-8')
    assert text.count('history_ctx ') == 2 and '客服小猫' in text
    assert 'model_ctx ' in text and 'understanding' in text and 'classifier' in text


async def test_default_app_creates_log_without_extra_cli_flags(monkeypatch, tmp_path, workflow_runtime):
    from contextlib import asynccontextmanager
    from mewhelp import main
    @asynccontextmanager
    async def runtime(*args, **kwargs):
        yield workflow_runtime
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(main, 'open_runtime', runtime)
    async with main.lifespan(main.app):
        await run_turn(workflow_runtime, TurnRequest(message='你好', session_id='default-log'))
        assert (tmp_path / 'log/app.log').read_text(encoding='utf-8').count('history_ctx ') == 1
    assert not hasattr(main.app.state, 'ch05_runtime')


async def test_summary_lifecycle_logs_boundaries_and_elapsed(tmp_path):
    from tests.ch07.test_summary_jobs import factory, HeldModel, job, manager_type
    from mewhelp.ch07.config import BudgetProfile
    from mewhelp.ch07.observability import configure_context_logging
    fixture = factory.__wrapped__(tmp_path)
    session_factory = next(fixture)
    try:
        path = tmp_path / 'summary.log'
        configure_context_logging(path)
        model = HeldModel()
        manager = manager_type()(session_factory, model, BudgetProfile())
        assert manager.schedule(job()) and not manager.schedule(job())
        await model.started.wait()
        model.release.set()
        await manager.aclose()
        text = path.read_text(encoding='utf-8')
        assert all(f'summary {phase}' in text for phase in ('start', 'done', 'skip'))
        assert 'cover=10..14' in text and 'elapsed_ms=' in text and '第1段' in text
    finally:
        fixture.close()
