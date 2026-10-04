"""Frozen synthetic labels and real upstream outputs, never a mock acceptance report."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

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
        if role == 'system':
            messages.append(SystemMessage(row['content']))
        elif role == 'user':
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
    if not 30 <= len(content) <= 200:
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
    import time

    from mewhelp.ch05.config import get_ch05_model
    for case in cases:
        started = time.perf_counter()
        attempts = []
        class RecordedUpstream:
            def __init__(self, model, recorded_attempts):
                self.model = model
                self.attempts = recorded_attempts
            async def ainvoke(self, messages):
                response = await self.model.ainvoke(messages)
                self.attempts.append({'content': str(response.content),
                    'usage': dict(response.usage_metadata or {}),
                    'response_model': response.response_metadata.get('model_name')})
                return response
        def model_factory(*args, _attempts=attempts, **kwargs):
            return RecordedUpstream(get_ch05_model(*args, **kwargs), _attempts)
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
        except Exception as error:  # noqa: BLE001 - failed upstream cases must remain in the report
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
        except Exception as error:  # noqa: BLE001 - report every failed provenance case
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


async def calibrate_tokens(dataset: Path, outdir: Path, *, reuse: Path | None = None) -> int:
    from dataclasses import asdict

    from langchain_core.utils.function_calling import convert_to_openai_tool

    from mewhelp.ch05.agent import build_read_registry
    from mewhelp.ch05.config import get_ch05_model
    from mewhelp.ch05.prompts import MAIN_SYSTEM

    from .budget import compute_budget
    from .tokens import estimate_request
    manifest = freeze_dataset(dataset)
    outdir.mkdir(parents=True, exist_ok=False)
    profile = BudgetProfile()
    cases = read_cases(dataset / 'tokens-calibration.jsonl')
    cases.append({'id':'production-prefix', 'messages':[{'role':'system','content':MAIN_SYSTEM},
        {'role':'user','content':'请只输出一个字：好。'}],
        'tools':[convert_to_openai_tool(t) for t in build_read_registry().tools()]})
    slots = asyncio.Semaphore(3)
    async def measure(case):
        async with slots:
            messages = case_messages(case['messages'])
            estimate = estimate_request(messages, case['tools'], profile=profile)
            try:
                model = get_ch05_model(16)
                if case['tools']:
                    model = model.bind_tools(case['tools'])
                response = await model.ainvoke(messages)
                usage = dict(response.usage_metadata or {})
                actual = usage.get('input_tokens', 0)
                row = {'id':case['id'], 'estimated':estimate, 'usage':usage,
                    'response_model':response.response_metadata.get('model_name'),
                    'underestimate':max(0, actual-estimate),
                    'errors':[] if actual else ['missing_real_usage']}
            except Exception as error:  # noqa: BLE001 - preserve failed token probe diagnostics
                row = {'id':case['id'], 'estimated':estimate, 'errors':[f'{type(error).__name__}: {error}']}
            print(case['id'] + ': ' + json.dumps(row, ensure_ascii=False), flush=True)
            return row
    reuse_audit = None
    if reuse:
        import subprocess
        previous = json.loads((reuse / 'summary.json').read_text(encoding='utf-8'))
        saved = read_cases(reuse / 'results.jsonl')
        if previous['dataset'] != manifest or [r['id'] for r in saved] != [c['id'] for c in cases]:
            raise ValueError('reuse inputs or frozen dataset changed')
        tools_source = 'src/mewhelp/tools/business.py'
        before = await asyncio.to_thread(subprocess.check_output, ['git', 'show', 'ed1a631:' + tools_source])
        if before.replace(b'\r\n', b'\n') != Path(tools_source).read_bytes().replace(b'\r\n', b'\n'):
            raise ValueError('reuse tools changed')
        prompt_source = 'src/mewhelp/ch05/prompts.py'
        before = await asyncio.to_thread(subprocess.check_output, ['git', 'show', 'ed1a631:' + prompt_source])
        prefix_changed = before.replace(b'\r\n', b'\n') != Path(prompt_source).read_bytes().replace(b'\r\n', b'\n')
        rows = []
        for case, original in zip(cases, saved):
            if case['id'] == 'production-prefix' and prefix_changed:
                rows.append(await measure(case))
                continue
            if original['errors'] or not original.get('usage', {}).get('input_tokens'):
                raise ValueError('reuse requires successful real upstream usage')
            estimate = estimate_request(case_messages(case['messages']), case['tools'], profile=profile)
            rows.append({**original, 'estimated':estimate,
                'underestimate':max(0, original['usage']['input_tokens']-estimate)})
        reuse_audit = {'from':str(reuse), 'raw_results_hash':hashlib.sha256((reuse/'results.jsonl').read_bytes()).hexdigest(),
            'source_commit':'ed1a631', 'unchanged_request_sources':[tools_source],
            'production_prefix_remeasured':prefix_changed, 'new_model_calls':int(prefix_changed),
            'current_prompt_hash':hashlib.sha256(MAIN_SYSTEM.encode()).hexdigest()}
        write_json(outdir / 'reuse-audit.json', reuse_audit)
    else:
        rows = await asyncio.gather(*(measure(case) for case in cases))
    (outdir / 'results.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows), encoding='utf-8')
    errors = [r for r in rows if r['errors'] or r.get('underestimate', 0)]
    demo = ContextSettings(model_context_window=18000, max_output_tokens=2000,
        max_user_input_tokens=2000, max_agent_steps=3, tool_result_max_tokens=1200, rerank_top_k=5, _env_file=None)
    report = {'complete':not errors, 'real_upstream':True, 'measurements':len(rows), 'errors':errors,
        'profile':asdict(profile), 'profile_hash':profile.fingerprint, 'dataset':manifest,
        'estimator_hash':hashlib.sha256(Path(__file__).with_name('tokens.py').read_bytes()).hexdigest(),
        'reuse_audit':reuse_audit,
        'budgets':{'default':asdict(compute_budget(ContextSettings(_env_file=None), profile)),
                   'demo':asdict(compute_budget(demo, profile))},
        'method':'Joint unchanged engineering package check: every measured input <= estimator; production prefix <= reserve; Chinese/ASCII coefficients and all reserves bound to one fingerprint.'}
    prefix = next((r for r in rows if r['id']=='production-prefix'), {})
    if prefix.get('usage', {}).get('input_tokens', 0) > profile.prefix_reserve:
        report['complete'] = False
        report['errors'].append({'id':'production-prefix-reserve','error':'measured prefix exceeds reserve'})
    write_json(outdir / 'summary.json', report)
    if report['complete']:
        write_json(outdir / 'context-profile.json', report)
    return 0 if report['complete'] else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['freeze', 'evaluate', 'calibrate-tokens'])
    parser.add_argument('--dataset', type=Path, default=Path('eval/ch07'))
    parser.add_argument('--part', default='summary')
    parser.add_argument('--phase', choices=['calibration', 'acceptance'], default='calibration')
    parser.add_argument('--outdir', type=Path)
    parser.add_argument('--reuse', type=Path, help='Reuse unchanged real token probes with a source audit')
    args = parser.parse_args()
    if args.command == 'freeze':
        print(json.dumps(freeze_dataset(args.dataset), ensure_ascii=False))
        return 0
    if not args.outdir:
        parser.error('--outdir required')
    if args.command == 'calibrate-tokens':
        return asyncio.run(calibrate_tokens(args.dataset, args.outdir, reuse=args.reuse))
    return asyncio.run(evaluate_part(args.dataset, args.outdir, part=args.part, phase=args.phase))


if __name__ == '__main__':
    raise SystemExit(main())
