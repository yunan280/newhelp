"""冻结标注、真实HTTP评估与六项验收失败合同。"""
import argparse
import asyncio
import hashlib
import json
import re
from pathlib import Path

import httpx

from mewhelp.ch06.evaluation import _digest, _read_cases, _write_json


def validate_acceptance(rows: list[dict]) -> dict:
    failures = []
    indexed = {row.get('requirement'):row for row in rows}
    if len(indexed) != 6 or set(indexed) != set(range(1, 7)) or len(rows) != 6:
        failures.append('必须覆盖六项验收且不重复')
    for number, row in indexed.items():
        audits = row.get('audits', [])
        if not audits:
            failures.append(f'{number}: 缺审计证据')
        for audit in audits:
            if any(audit.get(key) is None for key in ('tool_call_id','tool_name','tool_source','status','retry_count','duration_ms','created_at')):
                failures.append(f'{number}: 审计字段缺失')
            if audit.get('tool_source') == 'mcp' and not audit.get('mcp_server'):
                failures.append(f'{number}: 缺MCP来源')
            if audit.get('tool_source') not in ('builtin','mcp'):
                failures.append(f'{number}: 来源不合法')
        checks = {
            1: lambda row=row: row.get('tool_used') is True and row.get('registration_only') is True,
            2: lambda row=row, audits=audits: set(row.get('servers', [])) == {'logistics','aftersales'} and row.get('answers_grounded') is True and
               {a.get('mcp_server') for a in audits if a.get('tool_source') == 'mcp'} >= {'logistics','aftersales'},
            3: lambda row=row: all(row.get(k) is True for k in ('tool_used','customer_pid_unchanged','customer_code_unchanged','server_restarted')),
            4: lambda row=row, audits=audits: row.get('clarified') is True and row.get('preview') is True and row.get('tickets_delta') == 1 and
               bool(row.get('ticket_no')) and row['ticket_no'] in row.get('answer','') and
               any(a.get('tool_name') == 'create_ticket' and a.get('status') == '成功' for a in audits),
            5: lambda row=row, audits=audits: row.get('preview') is True and row.get('tickets_delta') == 0 and
               any(a.get('tool_name') == 'create_ticket' and a.get('status') == '权限拒绝' for a in audits),
            6: lambda row=row, audits=audits: row.get('honest_failure') is True and
               any(a.get('tool_name') != 'create_ticket' and a.get('status') == '超时' and a.get('retry_count') == 2 and a.get('duration_ms',0) > 0 for a in audits) and
               any(a.get('tool_name') == 'create_ticket' and a.get('status') == '超时' and a.get('retry_count') == 0 and a.get('duration_ms',0) > 0 for a in audits),
        }
        if number not in checks or not checks[number]():
            failures.append(f'{number}: 功能验收未满足')
    return {'passed':not failures,'requirements':len(indexed),'failures':failures}


def freeze(dataset: Path) -> dict:
    files = {}
    for path in sorted(dataset.rglob('*.jsonl')):
        cases = _read_cases(path)
        files[path.relative_to(dataset).as_posix()] = {'sha256':hashlib.sha256(path.read_text(encoding='utf-8').encode()).hexdigest(),
                            'count':len(cases),'split':cases[0]['split']}
    if not files:
        raise ValueError('没有标注样例')
    manifest = {'version':1,'files':files,'dataset_hash':_digest(files)}
    target = dataset/'freeze.json'
    if target.exists() and json.loads(target.read_text(encoding='utf-8')) != manifest:
        raise ValueError('冻结标签被改变，禁止覆盖掩盖失败')
    if not target.exists():
        _write_json(target, manifest)
    return manifest


def grade_case(case: dict, actual: dict, status_code: int = 200) -> list[str]:
    expected = case['expected']
    failures = []
    if 'observation' in case:
        if re.search(r'DSML|"reply_mode"\s*:|<tool_call', actual.get('answer', '')):
            failures.append('正文包含控制JSON或伪工具调用')
        if re.search(expected['forbidden'], actual.get('answer', '')):
            failures.append('工具失败后无依据宣称成功')
        if expected.get('clarify') and not re.search(r'请|问题|描述|什么|哪', actual.get('answer', '')):
            failures.append('缺少信息未追问')
        return failures
    if status_code != 200:
        return [f'HTTP {status_code}: {actual}']
    calls = [t['name'] for t in actual.get('tool_trace', [])]
    for key in ('intent', 'route'):
        if key in expected and actual.get(key) != expected[key]:
            failures.append(f'{key}: expected {expected[key]}, actual {actual.get(key)}')
    if expected.get('matched_tool') and expected['matched_tool'] not in [t['name'] for t in actual.get('tool_trace', []) if t['ok']]:
        failures.append('未执行匹配业务工具')
    if 'calls' in expected and calls != expected['calls']:
        failures.append(f'工具调用不符: {calls}')
    if 'preview' in expected and bool(actual.get('ticket_preview')) != expected['preview']:
        failures.append('工单预览不符')
    if expected.get('clarify') and actual.get('stop_reason') != 'clarification':
        failures.append('未追问缺失信息')
    if expected.get('failure_honest') and re.search(r'已查询成功|已成功创建|工单号\s*T\d', actual.get('answer', '')):
        failures.append('无依据宣称成功')
    return failures


async def run(dataset: Path, outdir: Path, base_url: str, *, resume_results: Path | None = None) -> int:
    manifest = freeze(dataset)
    outdir.mkdir(parents=True, exist_ok=False)
    rows = []
    cases = {case['id']:case for path in [*sorted(dataset.glob('*.jsonl')), dataset/'safety/feedback.jsonl'] for case in _read_cases(path)}
    if resume_results:
        for line in resume_results.read_text(encoding='utf-8').splitlines():
            row = json.loads(line)
            if cases.get(row['case']['id']) != row['case'] or row['case']['id'] in {r['case']['id'] for r in rows}:
                raise ValueError('恢复记录不属于当前冻结标签或重复')
            failures = grade_case(row['case'], row['actual'], row.get('status_code', 200))
            rows.append({**row, 'failures':failures, 'passed':not failures})
    completed = {row['case']['id'] for row in rows}
    def persist():
        (outdir/'results.jsonl').write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')
    persist()
    async with httpx.AsyncClient(base_url=base_url, timeout=240) as client:
        for path in sorted(dataset.glob('*.jsonl')):
            for case in _read_cases(path):
                if case['id'] in completed:
                    continue
                failures = []
                response = await client.post('/ch05/agent', json={'message':case['question'],
                    'user_id':'ch08-eval', 'session_id':f"{outdir.name}-{case['id']}"})
                actual = response.json()
                failures = grade_case(case, actual, response.status_code)
                rows.append({'case':case,'actual':actual,'failures':failures,'passed':not failures})
                persist()
                # Evaluation previews are abandoned, never confirm test prompts automatically.
                if actual.get('ticket_preview'):
                    await client.post('/ch08/tickets/resume', json={'session_id':actual['session_id'],
                        'user_id':'ch08-eval','confirmation_id':actual['ticket_preview']['confirmation_id'],'action':'cancel'})
    # Controlled tool feedback is prompt evaluation, with real provider output;
    # no fake business system, no side effects, and labels stay frozen.
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from mewhelp.ch05.agent import prompt_messages
    from mewhelp.ch05.config import get_ch05_model
    from mewhelp.ch08.ticket_intent import ticket_request_patch
    for case in _read_cases(dataset/'safety/feedback.jsonl'):
        if case['id'] in completed:
            continue
        call={'name':case['tool'],'args':case['args'],'id':case['id'],'type':'tool_call'}
        state={'question':case['question'],'messages':[HumanMessage(case['question'])],
            'agent_messages':[AIMessage('',tool_calls=[call]),ToolMessage(case['observation'],tool_call_id=case['id'],status='error')],
            'evidence':None,'decision':{'reply_mode':'clarify' if case['expected'].get('clarify') else 'answer','suggested_actions':[],'ticket_type':None},
            'ticket_request':ticket_request_patch(case['question'],case['id'],None)}
        try:
            response=await get_ch05_model(512).ainvoke(prompt_messages(state,phase='answer'))
            actual={'answer':str(response.content),'usage':response.usage_metadata}
            failures=grade_case(case, actual)
        except Exception as exc:  # noqa: BLE001 — provider故障作为失败样例保存，不掩盖
            actual={'error':str(exc)}
            failures=[f'{type(exc).__name__}: {exc}']
        rows.append({'case':case,'actual':actual,'failures':failures,'passed':not failures})
        persist()
    summary = {'passed':all(r['passed'] for r in rows),'total':len(rows),
               'failed':[r['case']['id'] for r in rows if not r['passed']],'dataset_hash':manifest['dataset_hash']}
    _write_json(outdir/'summary.json',summary)
    print(json.dumps(summary,ensure_ascii=False))
    return 0 if summary['passed'] else 1


async def calibrate(dataset: Path, outdir: Path):
    from mewhelp.ch06.evaluation import calibrate_router
    from mewhelp.ch08.runtime import create_tool_runtime
    from mewhelp.db.engine import SessionLocal
    tools = create_tool_runtime(SessionLocal)
    try:
        snapshot = await tools.refresh()
        return await calibrate_router(dataset/'router', outdir, tool_catalog=snapshot.catalog())
    finally:
        await tools.aclose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command',choices=('freeze','calibrate-router','run'))
    parser.add_argument('--dataset',type=Path,default=Path('eval/ch08'))
    parser.add_argument('--outdir',type=Path)
    parser.add_argument('--base-url',default='http://127.0.0.1:9020')
    parser.add_argument('--calibration',type=Path)
    parser.add_argument('--resume-results',type=Path)
    args = parser.parse_args()
    if args.command == 'freeze':
        print(json.dumps(freeze(args.dataset),ensure_ascii=False));return 0
    if args.outdir is None:
        parser.error('--outdir is required')
    if args.command == 'calibrate-router':
        return asyncio.run(calibrate(args.dataset,args.outdir))
    return asyncio.run(run(args.dataset,args.outdir,args.base_url,resume_results=args.resume_results))


if __name__ == '__main__':
    raise SystemExit(main())
