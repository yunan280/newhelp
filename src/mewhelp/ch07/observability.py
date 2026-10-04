import json
import logging
from dataclasses import asdict
from pathlib import Path

from langchain_core.utils.function_calling import convert_to_openai_tool

from .config import BudgetProfile
from .tokens import estimate_request, wire_messages
from .types import HistoryContext, SummaryJob

logger = logging.getLogger(__name__)
_NAMES = ('mewhelp.ch05', 'mewhelp.ch06', 'mewhelp.ch07')


def configure_context_logging(path: Path) -> None:
    path = path.resolve()
    owned = {handler for name in _NAMES for handler in logging.getLogger(name).handlers
             if getattr(handler, 'ch07_context_file', False)}
    if len(owned) == 1 and next(iter(owned)).baseFilename == str(path):
        return
    for name in _NAMES:
        current = logging.getLogger(name)
        for handler in owned:
            if handler in current.handlers:
                current.removeHandler(handler)
    for handler in owned:
        handler.close()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding='utf-8')
    handler.ch07_context_file = True
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    for name in _NAMES:
        current = logging.getLogger(name)
        current.setLevel(logging.INFO)
        current.addHandler(handler)


def log_model(messages, tools, *, state: dict, purpose: str, model_name: str,
              profile: BudgetProfile) -> dict:
    schemas = [convert_to_openai_tool(tool) for tool in tools]
    payload = {'purpose': purpose, 'model': model_name, 'session_id': state.get('session_id'),
        'turn_id': state.get('turn_id'), 'conversation_id': state.get('conversation_id'),
        'messages': wire_messages(messages), 'tools': schemas, 'window_count': len(messages),
        'tokens_estimate': estimate_request(messages, schemas, profile=profile),
        'profile_hash': profile.fingerprint,
        'message_ids': [message.id for message in messages]}
    logger.info('model_ctx %s', json.dumps(payload, ensure_ascii=False, separators=(',', ':')))
    return payload


def log_history(ctx: HistoryContext, *, state: dict, request_epoch: str) -> None:
    logger.info('history_ctx %s', json.dumps({'session_id': state.get('session_id'),
        'conversation_id': ctx.conversation_id, 'turn_id': state.get('turn_id'), 'epoch': request_epoch,
        'S': ctx.summary_upto_msg_id, 'L': ctx.layer1_from_msg_id, 'summary': ctx.summary,
        'summary_segments': [asdict(segment) for segment in ctx.summary_segments],
        'layer2': wire_messages(ctx.layer2), 'layer1': wire_messages(ctx.layer1),
        'message_ids': [m.id for m in (*ctx.layer2, *ctx.layer1)],
        'window_count': len(ctx.layer2) + len(ctx.layer1), 'tokens': ctx.tokens,
        'budget': asdict(ctx.budget)}, ensure_ascii=False, separators=(',', ':')))


def log_summary_event(phase: str, *, job: SummaryJob, **fields) -> None:
    logger.info('summary %s %s', phase, json.dumps({'conversation_id': job.conversation_id,
        'from_msg_id': job.turns[0].from_msg_id if job.turns else None,
        'upto_msg_id': job.layer1_snapshot_id, 'old_S': job.old_upto_msg_id, **fields},
        ensure_ascii=False, separators=(',', ':')))
