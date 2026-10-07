import asyncio
import logging
from dataclasses import asdict, replace
from uuid import uuid4

from langchain_core.messages import messages_from_dict, messages_to_dict

from .budget import ContextBudget, compute_budget, measured_prefix
from .observability import log_history
from .projection import group_committed_turns, project_history
from .store import advance_layer1, read_conversation
from .types import HistoryContext, HistoryTurn, SummaryJob, SummarySegment

logger = logging.getLogger(__name__)


def history_payload(history: HistoryContext) -> dict:
    return {'version': 1, 'conversation_id': history.conversation_id,
        'summary_upto_msg_id': history.summary_upto_msg_id,
        'layer1_from_msg_id': history.layer1_from_msg_id,
        'summary': history.summary, 'summary_segments': [asdict(s) for s in history.summary_segments],
        'layer2': messages_to_dict(history.layer2), 'layer1': messages_to_dict(history.layer1),
        'turns': [{'turn_id': t.turn_id, 'from_msg_id': t.from_msg_id, 'upto_msg_id': t.upto_msg_id,
                  'messages': messages_to_dict(t.messages)} for t in history.turns],
        'tokens': history.tokens, 'budget': asdict(history.budget)}


def history_from_payload(payload: dict) -> HistoryContext:
    if payload.get('version') != 1:
        raise ValueError('unsupported context payload')
    return HistoryContext(payload['conversation_id'], payload['summary_upto_msg_id'],
        payload['layer1_from_msg_id'], payload['summary'],
        tuple(SummarySegment(**s) for s in payload['summary_segments']),
        tuple(messages_from_dict(payload['layer2'])), tuple(messages_from_dict(payload['layer1'])),
        tuple(HistoryTurn(t['turn_id'], t['from_msg_id'], t['upto_msg_id'],
                          tuple(messages_from_dict(t['messages']))) for t in payload['turns']),
        payload['tokens'], ContextBudget(**payload['budget']))


def _snapshot(context, state):
    with context.session_factory() as session:
        return read_conversation(session, conversation_id=state['conversation_id'], user_id=state['user_id'])


def _advance(context, snapshot, history):
    with context.session_factory.begin() as session:
        return advance_layer1(session, conversation_id=snapshot.conversation_id,
            expected_layer1=snapshot.layer1_from_msg_id, new_layer1=history.layer1_from_msg_id)


async def prepare_history(context, state: dict) -> HistoryContext:
    snapshot = await asyncio.to_thread(_snapshot, context, state)
    turns = group_committed_turns(state.get('messages', []), snapshot, current_turn_id=state['turn_id'])
    from langchain_core.utils.function_calling import convert_to_openai_tool
    schemas = ([convert_to_openai_tool(t) for t in context.tool_snapshot.tools()]
               if context.tool_snapshot is not None else None)
    budget = compute_budget(context.settings, context.profile,
                            actual_fixed={'prefix': measured_prefix(context.profile, schemas)})
    history = project_history(turns, snapshot, budget, profile=context.profile)
    if history.layer1_from_msg_id > snapshot.layer1_from_msg_id:
        advanced = await asyncio.to_thread(_advance, context, snapshot, history)
        if not advanced:
            snapshot = await asyncio.to_thread(_snapshot, context, state)
            history = project_history(turns, snapshot, budget, profile=context.profile)
        else:
            logger.info('层1 降级 %s→%s conversation=%s tokens=%s budget=%s',
                snapshot.layer1_from_msg_id, history.layer1_from_msg_id, history.conversation_id,
                history.tokens['layer1_raw'], budget.layer1)
    if history.tokens['layer2_projected'] > budget.layer2 and context.summary_manager:
        logger.info('summary trigger 层2 约 %s token > 预算 %s conversation=%s cover=%s..%s elapsed_ms=0',
            history.tokens['layer2_projected'], budget.layer2, history.conversation_id,
            history.summary_upto_msg_id, history.layer1_from_msg_id)
        context.summary_manager.schedule(SummaryJob(history.conversation_id,
            history.summary_upto_msg_id, history.layer1_from_msg_id,
            tuple(t for t in turns if history.summary_upto_msg_id < t.upto_msg_id <= history.layer1_from_msg_id)))
    return history


async def prepare_request_context(context, state: dict):
    if context.tool_runtime is not None:
        context = replace(context, tool_snapshot=await context.tool_runtime.refresh())
    history = await prepare_history(context, state)
    payload = history_payload(history)
    epoch = uuid4().hex
    log_history(history, state=state, request_epoch=epoch)
    return replace(context, request_epoch=epoch, request_history=payload)


async def rebudget_history(context, state, *, actual_fixed):
    if not state.get('history_ctx'):
        return {}
    old = history_from_payload(state['history_ctx'])
    snapshot = await asyncio.to_thread(_snapshot, context, state)
    budget = compute_budget(context.settings, context.profile, actual_fixed=actual_fixed)
    projected = project_history(old.turns, snapshot, budget, profile=context.profile)
    if projected.layer1_from_msg_id > snapshot.layer1_from_msg_id:
        if await asyncio.to_thread(_advance, context, snapshot, projected):
            logger.info('层1 降级 %s→%s conversation=%s reason=actual_fixed tokens=%s budget=%s',
                snapshot.layer1_from_msg_id, projected.layer1_from_msg_id, snapshot.conversation_id,
                projected.tokens['layer1_raw'], budget.layer1)
        else:
            snapshot = await asyncio.to_thread(_snapshot, context, state)
            projected = project_history(old.turns, snapshot, budget, profile=context.profile)
    if projected.tokens['layer2_projected'] > budget.layer2 and context.summary_manager:
        logger.info('summary trigger 层2 约 %s token > 预算 %s conversation=%s cover=%s..%s elapsed_ms=0',
            projected.tokens['layer2_projected'], budget.layer2, projected.conversation_id,
            projected.summary_upto_msg_id, projected.layer1_from_msg_id)
        context.summary_manager.schedule(SummaryJob(projected.conversation_id,
            projected.summary_upto_msg_id, projected.layer1_from_msg_id,
            tuple(t for t in old.turns if projected.summary_upto_msg_id < t.upto_msg_id <= projected.layer1_from_msg_id)))
    return {'history_ctx': history_payload(projected)}


def tag_message(message, *, turn_id: str, ledger_id: int | None = None,
                from_msg_id: int = 0, upto_msg_id: int = 0, committed: bool = False):
    return message.model_copy(update={'additional_kwargs': {**message.additional_kwargs,
        'ch07': {'turn_id': turn_id, 'ledger_id': ledger_id, 'from_msg_id': from_msg_id,
                 'upto_msg_id': upto_msg_id, 'committed': committed}}})
