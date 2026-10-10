"""Verify saved acceptance evidence and current services without replaying LLM calls."""
import json
import math
from pathlib import Path

import httpx
from sqlalchemy import func, select

from mewhelp.db.engine import SessionLocal
from mewhelp.db.models import EvalRun, Ticket
from mewhelp.knowledge.answering import AnswerAssessment


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def main():
    root = Path('artifacts/ch09/20261008-native')
    proof = read(root / 'acceptance/final-evidence.json')
    verification = root / 'verification'
    assert '1120 passed, 20 deselected' in (verification / 'final-verified.log').read_text(encoding='utf-8')
    assert '6 passed' in (verification / 'final-affected-mysql.log').read_text(encoding='utf-8')
    assert len(proof['conversations']) == 15 and len(proof['traces']) == 20
    reviews = {row['id']: row for row in proof['reviews']}
    assert reviews['7']['occurrence_count'] == 2
    assert any(row['trigger_stage'] == 'feedback' and row['retrieved_chunks']
               for row in reviews['7']['originals']['items'])
    assert reviews['8']['review_status'] == '驳回'
    assert reviews['9']['originals']['items'][0]['retrieved_chunks'] is None
    assert reviews['10']['review_status'] == '通过'
    assert reviews['10']['originals']['items'][0]['retrieved_chunks']['chunks']
    conversations = {row['id']: row for row in proof['conversations']}
    assert any('验收卡[1]' in row['content'] for row in conversations['153']['messages'])
    assert conversations['157']['audits'][0]['status'] == '成功'
    assert conversations['161']['audits'][0]['status'] == '权限拒绝'
    assert all(a['retry_count'] == 0 for cid in ('157', '161') for a in conversations[cid]['audits'])
    assert conversations['162']['audits'][0]['tool_source'] == 'mcp'
    roots = read(root / 'acceptance/postfix-roots.json')
    assert len({row['traceId'] for row in roots}) == 3
    for row in roots:
        meta = row['metadata']
        if isinstance(meta, str):
            meta = json.loads(meta)
        assert meta['status'] == 'completed' and meta['session_id'] and meta['conversation_id']
    assert len(proof['token_costs']['items']) >= 2
    assert all(item['usage_coverage'] == 1 for item in proof['token_costs']['items'])
    assert proof['flywheel']['pending_count'] == 0 and proof['flywheel']['scan_error'] is None
    rounds = []
    for run_id in ('ch09_20261009_r01', 'ch09_20261009_r02'):
        folder = root / 'evaluations' / run_id
        summary = read(folder / 'summary.json')
        cases = [json.loads(line) for line in (folder / 'cases.jsonl').read_text(encoding='utf-8').splitlines()]
        assert len(cases) == summary['processed'] == summary['dataset_size'] == 40
        assert summary['complete'] and summary['metrics']['errors'] == 0
        valid_assessments = 0
        for case in cases:
            assert not case.get('error')
            if case['gate']['passed']:
                assessment = AnswerAssessment.model_validate(case['assessment'])
                if assessment.answerable:
                    assert not case['refused']
                    assert assessment.citation_numbers
                    assert all(1 <= number <= min(5, len(case['final']))
                               for number in assessment.citation_numbers)
                valid_assessments += 1
        assert valid_assessments == 33
        rounds.append({'run_id': run_id, 'cases': 40, 'valid_assessments': valid_assessments,
                       'metrics': summary['metrics']})
    trends = proof['trends']['items']
    assert len(trends) == 2 and trends[1]['previous_id'] == trends[0]['id']
    assert all(change['comparable'] and change['delta'] == 0 for change in trends[1]['changes'].values())
    database = {}
    with SessionLocal() as db:
        assert db.scalar(select(1)) == 1
        for cid, expected in ((157, 1), (161, 0)):
            actual = db.scalar(select(func.count()).select_from(Ticket).where(Ticket.conversation_id == cid))
            assert actual == expected
            database[f'tickets_conversation_{cid}'] = actual
        runs = list(db.scalars(select(EvalRun).order_by(EvalRun.id)))
        assert len(runs) == 2
        for run, saved in zip(runs, rounds, strict=True):
            assert run.dataset_size == 40
            assert run.metrics['_meta']['run_id'] == saved['run_id']
            assert math.isclose(run.metrics['faithfulness'], saved['metrics']['faithfulness'], rel_tol=1e-15)
        database['eval_run_ids'] = [row.id for row in runs]
    health = {}
    with httpx.Client(timeout=10) as client:
        for url in ('http://127.0.0.1:9030/healthz', 'http://127.0.0.1:9020/healthz',
                    'http://127.0.0.1:3039/api/public/health'):
            response = client.get(url)
            response.raise_for_status()
            health[url] = response.status_code
    report = {'model_requests_replayed': 0, 'full_unit_tests': 1120,
              'affected_mysql_tests': 6, 'conversations': 15, 'traces': 20,
              'rounds': rounds, 'database': database, 'health': health,
              'browser_last_attempt': 'stopped by browser URL enforcement; prior real UI evidence retained'}
    (verification / 'delivery-check.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Delivery verified: 1120 unit tests; 6 affected MySQL tests; 80 real evaluation cases; '
          '15 conversations; 20 traces; confirm/cancel database receipts; 3 services healthy; 0 LLM calls replayed.')


if __name__ == '__main__':
    main()
