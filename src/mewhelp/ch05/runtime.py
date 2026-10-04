from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from functools import lru_cache

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

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
):
    @lru_cache(maxsize=1)
    def default_rag_factory():
        calibration = get_settings().rag_calibration_path
        if calibration is None:
            raise RuntimeError("RAG_CALIBRATION_PATH must be explicitly configured")
        return get_rag_runtime(session_factory, calibration_path=calibration)

    context_settings = context_settings or ContextSettings()
    profile = context_profile or load_profile(context_settings.context_calibration_path)
    try:
        compute_budget(context_settings, profile, actual_fixed={'prefix': measured_prefix(profile)})
    except ContextBudgetError:
        import logging
        logging.getLogger(__name__).exception('上下文预算不足：启动自检失败')
        raise
    manager = SummaryTaskManager(session_factory,
        ChatSummaryModel(context_settings, profile, model_factory=model_factory), profile,
        settings=context_settings)
    settings.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_path)) as saver:
        context = WorkflowContext(
            session_factory,
            model_factory,
            rag_factory or default_rag_factory,
            replace(settings.limits(), max_tools=context_settings.max_agent_steps,
                    max_decisions=context_settings.max_agent_steps + 2,
                    final_max_tokens=context_settings.max_output_tokens),
            **({"router_settings": router_settings} if router_settings is not None else {}),
            router_model_factory=router_model_factory,
            settings=context_settings, profile=profile, summary_manager=manager,
        )
        try:
            yield WorkflowRuntime(build_workflow(saver), context, SessionStore())
        finally:
            await manager.aclose()
