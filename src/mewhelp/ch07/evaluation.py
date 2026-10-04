"""Frozen synthetic labels and real upstream outputs, never a mock acceptance report."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from .config import BudgetProfile, ContextSettings
from .prompts import EMPTY_SUMMARY, SUMMARY_SYSTEM
from .summary_model import ChatSummaryModel, numeric_facts


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')


def read_cases(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


def freeze_dataset(path: Path) -> dict:
    files = {}
    for file in sorted(path.glob('*.jsonl')):
        rows = read_cases(file)
        if not rows or len({r['id'] for r in rows}) != len(rows):
            raise ValueError('invalid frozen labels')
        if any(not isinstance(r.get('expected'), dict) or r.get('split') not in
               {'calibration', 'acceptance'} for r in rows):
            raise ValueError('invalid frozen labels')
        files[file.name] = {'sha256': hashlib.sha256(file.read_bytes()).hexdigest(),
                            'count': len(rows), 'split': rows[0]['split']}
    manifest = {'version': 1, 'files': files}
    target = path / 'freeze.json'
    if target.exists() and json.loads(target.read_text(encoding='utf-8')) != manifest:
        raise ValueError('frozen labels changed; never refreeze to conceal failures')
    if not target.exists():
        write_json(target, manifest)
    return manifest


def case_messages(rows):
    messages = []
    for row in rows:
        role = row['role']
        if role == 'user':
            messages.append(HumanMessage(row['content']))
        elif role == 'assistant':
            messages.append(AIMessage(row['content'], tool_calls=row.get('tool_calls', [])))
        elif role == 'tool':
            messages.append(ToolMessage(row['content'], tool_call_id=row['tool_call_id'],
                                         name=row.get('name')))
        else:
            raise ValueError('invalid batch role')
    return messages


def grade_summary(case, content):
    expected = case['expected']
    errors = []
    if expected['empty']:
        if content != EMPTY_SUMMARY:
            errors.append('chitchat_not_empty')
        return errors
    if not 50 <= len(content) <= 200:
        errors.append('length')
    for number in expected['required_digits']:
        if number not in content:
            errors.append(f'missing_identifier:{number}')
    source = json.dumps(case['batch'], ensure_ascii=False)
    if numeric_facts(content) - numeric_facts(source):
        errors.append('invented_numeric_fact_or_old_background')
    for group in expected['any_groups']:
        if not any(word in content for word in group):
            errors.append(f'missing_fact:{group}')
    for word in expected['forbidden']:
        if word in content:
            errors.append(f'forbidden:{word}')
    return errors


async def evaluate_part(dataset, outdir, *, part: str, phase: str) -> int:
    manifest = freeze_dataset(dataset)
    if part == 'references':
        return await evaluate_references(dataset, outdir, phase=phase, manifest=manifest)
    if part != 'summary':
        raise ValueError('reference evaluation requires integrated provenance (Task 8)')
    cases = read_cases(dataset / f'{part}-{phase}.jsonl')
    outdir.mkdir(parents=True, exist_ok=False)
    profile = BudgetProfile()
    results = []
    # Preserve raw candidate outputs even when validation fails; evaluation grades
    # after the real request rather than discarding the evidence.
    from mewhelp.ch05.config import get_ch05_model
    import time
    for case in cases:
        started = time.perf_counter()
        attempts = []
        class RecordedUpstream:
            def __init__(self, model):
                self.model = model
            async def ainvoke(self, messages):
                response = await self.model.ainvoke(messages)
                attempts.append({'content': str(response.content),
                    'usage': dict(response.usage_metadata or {}),
                    'response_model': response.response_metadata.get('model_name')})
                return response
        def model_factory(*args, **kwargs):
            return RecordedUpstream(get_ch05_model(*args, **kwargs))
        model = ChatSummaryModel(ContextSettings(), profile, model_factory=model_factory)
        try:
            response = await model.summarize(batch=case_messages(case['batch']),
                                              background=case['background'])
            content = response.content
            errors = grade_summary(case, content)
            usage = response.usage
            if not usage.get('input_tokens') or not usage.get('output_tokens'):
                errors.append('missing_real_usage')
            row = {'id': case['id'], 'content': content, 'usage': usage,
                   'response_model': attempts[-1]['response_model'], 'errors': errors}
        except Exception as error:
            row = {'id': case['id'], 'errors': [f'{type(error).__name__}: {error}']}
        row['elapsed_ms'] = round((time.perf_counter() - started) * 1000)
        row['attempts'] = attempts
        results.append(row)
        with (outdir / 'results.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        print(f'{case["id"]}: {"PASS" if not row["errors"] else row["errors"]}', flush=True)
    passed = sum(not r['errors'] for r in results)
    write_json(outdir / 'summary.json', {'part': part, 'phase': phase, 'passed': passed,
        'total': len(results), 'complete': len(results) == len(cases), 'real_upstream': True,
        'dataset': manifest, 'prompt_hash': hashlib.sha256(SUMMARY_SYSTEM.encode()).hexdigest(),
        'profile_hash': profile.fingerprint, 'settings': model.settings.model_dump(mode='json')})
    return 0 if passed == len(cases) else 1


async def evaluate_references(dataset, outdir, *, phase, manifest):
    import time
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from mewhelp.ch05.limits import AgentLimits
    from mewhelp.ch05.state import WorkflowContext
    from mewhelp.ch06.understanding import understand_query
    from mewhelp.db.base import Base
    from mewhelp.db.models import Conversation, ConversationSummary, Message, MsgRole
    from .budget import compute_budget
    from .context import history_payload
    from .types import HistoryContext, SummarySegment
    outdir.mkdir(parents=True, exist_ok=False)
    engine = create_engine(f'sqlite:///{(outdir / "provenance.sqlite").resolve().as_posix()}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    profile, settings = BudgetProfile(), ContextSettings()
    results = []
    code_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    for case in read_cases(dataset / f'references-{phase}.jsonl'):
        try:
            with factory.begin() as session:
                conv = Conversation(session_id=case['id'], user_id='eval-ch07')
                session.add(conv)
                session.flush()
                cid = conv.id
                rows = [Message(conversation_id=cid, role=role, content=content)
                    for role, content in [(MsgRole.user, f'订单{case["orders"][0]}查物流'),
                        (MsgRole.assistant, '未解决'), (MsgRole.user, f'订单{case["orders"][1]}问保修'),
                        (MsgRole.assistant, '未解决')]]
                session.add_all(rows)
                session.flush()
                segment = ConversationSummary(conversation_id=cid, seq=1,
                    from_msg_id=rows[0].id, upto_msg_id=rows[-1].id, content=case['summary'])
                session.add(segment)
                session.flush()
                ctx = HistoryContext(cid, rows[-1].id, rows[-1].id, case['summary'],
                    (SummarySegment(segment.id, 1, rows[0].id, rows[-1].id, case['summary']),),
                    (), (), (), {}, compute_budget(settings, profile))
            context = WorkflowContext(factory, lambda: None, lambda: None, AgentLimits())
            result = await understand_query(case['question'], [], [], context=context,
                state={'user_id': 'eval-ch07', 'history_ctx': history_payload(ctx), 'started_at': time.time()})
            errors = []
            if result.trusted_order_id != case['expected']['order_id']:
                errors.append('wrong_reference')
            if numeric_facts(result.question) - (numeric_facts(case['question']) |
                ({case['expected']['order_id']} if case['expected']['order_id'] else set())):
                errors.append('invented_number')
            if case['expected']['negation_preserved'] and '不' not in result.question:
                errors.append('negation_lost')
            if result.usage.estimated or not result.usage.input_tokens:
                errors.append('missing_real_usage')
            row = {'id': case['id'], **result.evaluation_result(), 'errors': errors}
        except Exception as error:
            row = {'id': case['id'], 'errors': [f'{type(error).__name__}: {error}']}
        results.append(row)
        with (outdir / 'results.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
        print(f'{case["id"]}: {"PASS" if not row["errors"] else row["errors"]}', flush=True)
    engine.dispose()
    passed = sum(not row['errors'] for row in results)
    write_json(outdir / 'summary.json', {'part': 'references', 'phase': phase, 'passed': passed,
        'total': len(results), 'complete': True, 'real_upstream': True, 'dataset': manifest,
        'code_hash': code_hash, 'profile_hash': profile.fingerprint})
    return 0 if passed == len(results) else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['freeze', 'evaluate'])
    parser.add_argument('--dataset', type=Path, default=Path('eval/ch07'))
    parser.add_argument('--part', default='summary')
    parser.add_argument('--phase', choices=['calibration', 'acceptance'], default='calibration')
    parser.add_argument('--outdir', type=Path)
    args = parser.parse_args()
    if args.command == 'freeze':
        print(json.dumps(freeze_dataset(args.dataset), ensure_ascii=False))
        return 0
    if not args.outdir:
        parser.error('--outdir required')
    return asyncio.run(evaluate_part(args.dataset, args.outdir, part=args.part, phase=args.phase))


if __name__ == '__main__':
    raise SystemExit(main())
