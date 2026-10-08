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
        if self.settings.enabled:
            from .dedup import find_equivalent
            from .flywheel import FlywheelWorker
            from .normalization import normalize_gap

            async def normalize(original, *, snapshot):
                return await normalize_gap(
                    original,
                    snapshot=snapshot,
                    model=runtime.context.model_factory(1024, streaming=False),
                )

            async def dedup(question, *, candidates):
                return await find_equivalent(
                    question,
                    candidates=candidates,
                    model=runtime.context.model_factory(1024, streaming=False),
                )

            self.flywheel = FlywheelWorker(
                self.session_factory,
                normalize=normalize,
                dedup=dedup,
                interval=self.settings.worker_interval_seconds,
                observations=self.observation_runtime,
            )
            self.flywheel.start()

    async def aclose_workers(self):
        if self.flywheel:
            await self.flywheel.aclose()


@asynccontextmanager
async def open_ch09_runtime(factory, *, settings):
    observations = ObservationRuntime(settings=settings)
    previous = set_default_runtime(observations)
    runtime = Ch09Runtime(observations, factory, settings)
    try:
        yield runtime
    finally:
        await runtime.aclose_workers()
        set_default_runtime(previous)
        await asyncio.to_thread(observations.shutdown)
