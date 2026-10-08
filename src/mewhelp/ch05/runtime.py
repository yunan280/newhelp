import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from functools import lru_cache

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from mewhelp.ch06.config import Ch06Settings
from mewhelp.ch07.budget import ContextBudgetError, compute_budget, measured_prefix
from mewhelp.ch07.config import ContextSettings, load_profile
from mewhelp.ch07.summary import SummaryTaskManager
from mewhelp.ch07.summary_model import ChatSummaryModel
from mewhelp.config import get_settings
from mewhelp.knowledge.answering import get_rag_runtime
from mewhelp.memory import SessionStore

from .config import Ch05Settings, get_ch05_model
from .state import WorkflowContext
from .workflow import build_workflow


@dataclass
class WorkflowRuntime:
    graph: object
    context: WorkflowContext
    locks: SessionStore


@asynccontextmanager
async def open_runtime(
    session_factory,
    *,
    settings: Ch05Settings,
    model_factory=get_ch05_model,
    rag_factory=None,
    router_settings=None,
    router_model_factory=None,
    context_settings=None,
    context_profile=None,
    tool_runtime=None,
    observation_runtime=None,
    confidence_profile=None,
    ch09_settings=None,
):
    @lru_cache(maxsize=1)
    def default_rag_factory():
        calibration = get_settings().rag_calibration_path
        if calibration is None:
            raise RuntimeError("RAG_CALIBRATION_PATH must be explicitly configured")
        return get_rag_runtime(session_factory, calibration_path=calibration)

    context_settings = context_settings or ContextSettings()
    if confidence_profile is None:
        from mewhelp.ch09.confidence import load_workflow_confidence
        confidence_profile = load_workflow_confidence(ch09_settings)
    router_settings = router_settings or Ch06Settings()
    profile = context_profile or load_profile(context_settings.context_calibration_path)
    try:
        compute_budget(context_settings, profile, actual_fixed={'prefix': measured_prefix(profile)})
    except ContextBudgetError:
        logging.getLogger(__name__).exception('上下文预算不足：启动自检失败')
        raise
    limits = replace(settings.limits(), max_tools=context_settings.max_agent_steps,
                     max_decisions=context_settings.max_agent_steps + 2,
                     final_max_tokens=context_settings.max_output_tokens)
    if 'total_model_tokens' not in settings.model_fields_set:
        # Each structured purpose permits one repair. Understanding, expansion,
        # assessment and one/two classifier models bound the control calls.
        control_calls = 2 * (3 + (2 if router_settings.cascade_enabled else 1))
        max_calls = control_calls + limits.max_decisions + 1  # final answer
        limits = replace(limits, total_model_tokens=context_settings.model_context_window * max_calls)
    logging.getLogger(__name__).info('turn cost budget tokens=%s window=%s explicit=%s',
        limits.total_model_tokens, context_settings.model_context_window,
        'total_model_tokens' in settings.model_fields_set)
    manager = SummaryTaskManager(session_factory,
        ChatSummaryModel(context_settings, profile, model_factory=model_factory), profile,
        settings=context_settings)
    settings.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_path)) as saver:
        context = WorkflowContext(
            session_factory,
            model_factory,
            rag_factory or default_rag_factory,
            limits,
            router_settings=router_settings,
            router_model_factory=router_model_factory,
            settings=context_settings, profile=profile, summary_manager=manager,
            tool_runtime=tool_runtime,
            observation_runtime=observation_runtime,
            confidence_profile=confidence_profile,
        )
        try:
            callbacks = observation_runtime.callbacks if observation_runtime else ()
            yield WorkflowRuntime(build_workflow(saver, callbacks=callbacks), context, SessionStore())
        finally:
            await manager.aclose()
