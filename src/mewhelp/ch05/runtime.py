from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

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
):
    @lru_cache(maxsize=1)
    def default_rag_factory():
        calibration = get_settings().rag_calibration_path
        if calibration is None:
            raise RuntimeError("RAG_CALIBRATION_PATH must be explicitly configured")
        return get_rag_runtime(session_factory, calibration_path=calibration)

    settings.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    async with AsyncSqliteSaver.from_conn_string(str(settings.checkpoint_path)) as saver:
        context = WorkflowContext(
            session_factory,
            model_factory,
            rag_factory or default_rag_factory,
            settings.limits(),
            **({"router_settings": router_settings} if router_settings is not None else {}),
            router_model_factory=router_model_factory,
        )
        yield WorkflowRuntime(build_workflow(saver), context, SessionStore())
