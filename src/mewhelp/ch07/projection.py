import json
import logging
from collections import OrderedDict
from collections.abc import Sequence

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage, trim_messages

from .budget import ContextBudget
from .config import BudgetProfile
from .store import latest_segments
from .tokens import estimate_messages, estimate_text
from .types import ConversationSnapshot, HistoryContext, HistoryTurn

logger = logging.getLogger(__name__)


def _meta(message):
    return message.additional_kwargs.get('ch07', {})


def _visible_turns(snapshot, excluded):
    groups = []
    pending = []
    for row in snapshot.messages:
        if row.id in excluded:
            continue
        if row.role == 'user':
            if pending and pending[-1].role == 'assistant':
                groups.append(pending)
            pending = [row]
        elif pending:
            pending.append(row)
    if pending and pending[-1].role == 'assistant':
        groups.append(pending)
    return groups


def group_committed_turns(full: Sequence[AnyMessage], snapshot: ConversationSnapshot,
                          *, current_turn_id: str) -> tuple[HistoryTurn, ...]:
    ledger = {row.id: row for row in snapshot.messages}
    tagged = OrderedDict()
    excluded = set()
    for message in full:
        meta = _meta(message)
        turn_id = meta.get('turn_id')
        if turn_id == current_turn_id:
            start, end = meta.get('from_msg_id', 0), meta.get('upto_msg_id', 0)
            excluded.update(ident for ident in ledger if start and start <= ident <= end)
            if meta.get('ledger_id'):
                excluded.add(meta['ledger_id'])
        elif turn_id:
            tagged.setdefault(turn_id, []).append(message)
    committed = []
    covered = set(excluded)
    for turn_id, messages in tagged.items():
        metas = [_meta(m) for m in messages if _meta(m).get('committed')]
        starts = [m.get('from_msg_id', 0) for m in metas if m.get('from_msg_id')]
        ends = [m.get('upto_msg_id', 0) for m in metas if m.get('upto_msg_id')]
        if not starts or not ends:
            continue
        start, end = min(starts), max(ends)
        if (start not in ledger or end not in ledger or ledger[start].role != 'user'
            or ledger[end].role != 'assistant'):
            continue
        committed.append(HistoryTurn(turn_id, start, end, tuple(messages)))
        covered.update(ident for ident in ledger if start <= ident <= end)
    # Older checkpoints have no provenance. Only uniquely matched visible messages
    # may supply the tool trace; ambiguity falls back to the authoritative ledger.
    for rows in _visible_turns(snapshot, covered):
        positions = []
        for row in rows:
            positions.append([i for i, message in enumerate(full)
                if not _meta(message).get('turn_id') and (
                    message.id == f'mysql-{row.id}' or (
                        message.type == ('human' if row.role == 'user' else 'ai')
                        and not getattr(message, 'tool_calls', []) and message.content == row.content))])
        if all(len(p) == 1 for p in positions) and all(
            positions[i][0] < positions[i + 1][0] for i in range(len(positions) - 1)):
            messages = tuple(full[positions[0][0]:positions[-1][0] + 1])
        else:
            logger.info('history fallback conversation=%s range=%s..%s ambiguous legacy checkpoint',
                        snapshot.conversation_id, rows[0].id, rows[-1].id)
            messages = tuple((HumanMessage if r.role == 'user' else AIMessage)(r.content,
                id=f'mysql-{r.id}', additional_kwargs={'ch07': {'ledger_id': r.id,
                'turn_id': f'ledger-{rows[0].id}', 'from_msg_id': rows[0].id,
                'upto_msg_id': rows[-1].id, 'committed': True}}) for r in rows)
        committed.append(HistoryTurn(f'ledger-{rows[0].id}', rows[0].id, rows[-1].id, messages))
    return tuple(sorted(committed, key=lambda t: t.from_msg_id))


def _layer2(turn: HistoryTurn, profile: BudgetProfile) -> tuple[AnyMessage, ...]:
    projected = []
    calls = {}
    for message in turn.messages:
        if isinstance(message, AIMessage):
            calls.update({call['id']: call for call in message.tool_calls})
            content = message.content
            if isinstance(content, str) and len(content) > 60:
                message = message.model_copy(update={'content': content[:60] + '…[已截短]'})
        elif isinstance(message, ToolMessage) and estimate_text(str(message.content), profile=profile) > profile.steady_tool_tokens:
            call = calls.get(message.tool_call_id, {})
            name = message.name or call.get('name', 'unknown')
            try:
                payload = json.loads(str(message.content))
            except (ValueError, TypeError):
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            obj = payload.get('order_id') or payload.get('product_id')
            if not obj:
                args = call.get('args', {})
                obj = args.get('order_id') or args.get('product_id')
            status = payload.get('status', message.status)
            safe = lambda value: str(value).replace('\n', ' ').replace('\r', ' ')[:80]
            marker = f'[工具结果 name={safe(name)} call_id={safe(message.tool_call_id)}'
            if obj is not None:
                marker += f' object={safe(obj)}'
            marker += f' status={safe(status)}]'
            message = message.model_copy(update={'content': marker})
        projected.append(message)
    return tuple(projected)


def project_history(turns: Sequence[HistoryTurn], snapshot: ConversationSnapshot,
                    budget: ContextBudget, *, profile: BudgetProfile) -> HistoryContext:
    available = tuple(t for t in turns if t.upto_msg_id > snapshot.summary_upto_msg_id)
    recent = [t for t in available if t.upto_msg_id > snapshot.layer1_from_msg_id]
    raw = [m for t in recent for m in t.messages]
    counter = lambda messages: estimate_messages(messages, profile=profile)
    if raw and counter(raw) > budget.layer1:
        trimmed = trim_messages(raw, max_tokens=budget.layer1, token_counter=counter,
            strategy='last', start_on='human', include_system=False, allow_partial=False)
        # trim_messages may leave a partial turn. Retain only whole suffix turns.
        keep_ids = {id(m) for m in trimmed}
        while recent and (not all(id(m) in keep_ids for m in recent[0].messages)
                          or counter([m for t in recent for m in t.messages]) > budget.layer1):
            recent.pop(0)
    kept_starts = {t.from_msg_id for t in recent}
    dropped = [t for t in available if t.from_msg_id not in kept_starts]
    layer1_from = max([snapshot.layer1_from_msg_id, *(t.upto_msg_id for t in dropped)])
    l1 = tuple(m for t in recent for m in t.messages)
    l2 = tuple(m for t in available if t.upto_msg_id <= layer1_from for m in _layer2(t, profile))
    segments = latest_segments(snapshot.summaries, profile=profile)
    summary = '\n'.join(s.content for s in segments)
    tokens = {'layer1_raw': counter(l1) if l1 else 0,
              'layer2_projected': counter(l2) if l2 else 0,
              'summary': estimate_text(summary, profile=profile)}
    tokens['total'] = sum(tokens.values())
    return HistoryContext(snapshot.conversation_id, snapshot.summary_upto_msg_id,
        layer1_from, summary, segments, l2, l1, tuple(turns), tokens, budget)


def model_messages(history: HistoryContext, *, system: str, question: str,
                   background: dict, current_react: Sequence[AnyMessage] = ()) -> list[AnyMessage]:
    payload = {'summary': history.summary, 'summary_sources': [
        {'id': s.id, 'seq': s.seq, 'from_msg_id': s.from_msg_id, 'upto_msg_id': s.upto_msg_id,
         } for s in history.summary_segments], **background}
    return [SystemMessage(system), *history.layer2, *history.layer1, HumanMessage(question),
            HumanMessage(json.dumps(payload, ensure_ascii=False, separators=(',', ':'))), *current_react]
