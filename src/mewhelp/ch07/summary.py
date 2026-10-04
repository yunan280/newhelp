import asyncio
import logging
import time
from dataclasses import replace

from mewhelp.db.models import Conversation

from .config import BudgetProfile, ContextSettings
from .store import append_summary
from .summary_model import SummaryModel, summary_messages, validate_summary
from .tokens import estimate_messages
from .types import SummaryJob

logger = logging.getLogger(__name__)


class SummaryTaskManager:
    def __init__(self, session_factory, model: SummaryModel, profile: BudgetProfile,
                 *, concurrency: int = 2, settings: ContextSettings | None = None):
        if concurrency < 1:
            raise ValueError('summary concurrency must be positive')
        self.session_factory = session_factory
        self.model = model
        self.profile = profile
        self.settings = settings or ContextSettings()
        self.inflight: dict[int, asyncio.Task] = {}
        self._slots = asyncio.Semaphore(concurrency)
        self._closed = False

    def schedule(self, job: SummaryJob) -> bool:
        if self._closed or job.conversation_id in self.inflight or not job.turns:
            logger.info('summary skip conversation=%s cover=%s..%s reason=%s elapsed_ms=0',
                job.conversation_id, job.old_upto_msg_id, job.layer1_snapshot_id,
                'closed' if self._closed else 'inflight' if job.conversation_id in self.inflight else 'empty')
            return False
        task = asyncio.create_task(self._run(job), name=f'summary-{job.conversation_id}')
        self.inflight[job.conversation_id] = task
        return True

    def _background(self, job):
        with self.session_factory() as session:
            conv = session.get(Conversation, job.conversation_id)
            if conv is None or (conv.summary_upto_msg_id or 0) != job.old_upto_msg_id:
                return None
            return conv.summary or ''

    def _commit(self, job, result):
        with self.session_factory.begin() as session:
            return append_summary(session, job=job, from_msg_id=job.turns[0].from_msg_id,
                upto_msg_id=job.turns[-1].upto_msg_id, content=result.content, profile=self.profile)

    def _batches(self, job, background):
        reserve = 256 + self.profile.safety_reserve + self.profile.control_reserve
        allowance = self.settings.model_context_window - reserve
        batches = []
        pending = []
        for turn in job.turns:
            candidate = [*pending, turn]
            messages = [m for t in candidate for m in t.messages]
            if estimate_messages(summary_messages(messages, background=background), profile=self.profile) > allowance:
                if not pending:
                    raise ValueError('single raw history turn exceeds summary window')
                batches.append(tuple(pending))
                pending = [turn]
                if estimate_messages(summary_messages(turn.messages, background=background), profile=self.profile) > allowance:
                    raise ValueError('single raw history turn exceeds summary window')
            else:
                pending = candidate
        if pending:
            batches.append(tuple(pending))
        return batches

    async def _run(self, job):
        started = time.perf_counter()
        try:
            async with self._slots:
                background = await asyncio.to_thread(self._background, job)
                if background is None:
                    logger.info('summary skip conversation=%s cover=%s..%s reason=stale elapsed_ms=%s',
                        job.conversation_id, job.old_upto_msg_id, job.layer1_snapshot_id,
                        round((time.perf_counter() - started) * 1000))
                    return
                old = job.old_upto_msg_id
                # Background is fixed at trigger time. Earlier new sub-batches are
                # never fed back into the next summary model call.
                for turns in self._batches(job, background):
                    subjob = replace(job, old_upto_msg_id=old, turns=turns)
                    logger.info('summary start conversation=%s cover=%s..%s trigger_L=%s',
                        job.conversation_id, turns[0].from_msg_id, turns[-1].upto_msg_id,
                        job.layer1_snapshot_id)
                    batch = tuple(m for turn in turns for m in turn.messages)
                    result = await self.model.summarize(batch=batch, background=background)
                    validate_summary(result, batch)
                    segment = await asyncio.to_thread(self._commit, subjob, result)
                    if segment is None:
                        logger.info('summary skip conversation=%s cover=%s..%s reason=stale elapsed_ms=%s',
                            job.conversation_id, turns[0].from_msg_id, turns[-1].upto_msg_id,
                            round((time.perf_counter() - started) * 1000))
                        return
                    old = segment.upto_msg_id
                    logger.info('summary done 第%s段 conversation=%s cover=%s..%s trigger_L=%s elapsed_ms=%s content=%s',
                        segment.seq, job.conversation_id, segment.from_msg_id, segment.upto_msg_id,
                        job.layer1_snapshot_id, round((time.perf_counter() - started) * 1000), segment.content)
        except asyncio.CancelledError:
            logger.info('summary skip conversation=%s cover=%s..%s reason=cancelled elapsed_ms=%s',
                job.conversation_id, job.old_upto_msg_id, job.layer1_snapshot_id,
                round((time.perf_counter() - started) * 1000))
            raise
        except Exception:
            logger.exception('summary fail conversation=%s cover=%s..%s elapsed_ms=%s',
                job.conversation_id, job.old_upto_msg_id, job.layer1_snapshot_id,
                round((time.perf_counter() - started) * 1000))
        finally:
            self.inflight.pop(job.conversation_id, None)

    async def aclose(self, *, timeout_seconds: float = 5) -> None:
        self._closed = True
        tasks = list(self.inflight.values())
        if not tasks:
            return
        _, pending = await asyncio.wait(tasks, timeout=timeout_seconds)
        for task in pending:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
