import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

from .observability import ObservationRuntime, set_default_runtime


@dataclass
class Ch09Runtime:
    observation_runtime: ObservationRuntime
    session_factory: object
    settings: object
    flywheel: object | None = None
    eval_jobs: object | None = None
    workflow: object | None = None

    async def attach_workflow(self, runtime):
        self.workflow = runtime


@asynccontextmanager
async def open_ch09_runtime(factory, *, settings):
    observations = ObservationRuntime(settings=settings)
    previous = set_default_runtime(observations)
    try:
        yield Ch09Runtime(observations, factory, settings)
    finally:
        set_default_runtime(previous)
        await asyncio.to_thread(observations.shutdown)
