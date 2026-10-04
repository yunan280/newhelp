"""Actual HTTP/SSE, MySQL originals and app.log acceptance; no mock pass shortcut."""
import argparse
import asyncio
import datetime as dt
import json
import re
import tempfile
import time
from itertools import pairwise
from pathlib import Path

import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from mewhelp.ch07.config import BudgetProfile
from mewhelp.ch07.evaluation import write_json
from mewhelp.ch07.summary import SummaryTaskManager
from mewhelp.ch07.types import HistoryTurn, SummaryJob, SummaryResult
from mewhelp.db.base import Base
from mewhelp.db.models import Conversation, ConversationSummary, Message, MsgRole


def grade_report(report):
    errors = list(report.get('errors', []))
    for key in ('real_http_sse', 'real_usage', 'mysql_originals_match', 'immutable_segments', 'monotonic_anchors'):
        if report.get(key) is not True:
            errors.append('missing evidence: ' + key)
    if report.get('turns_completed', 0) < 22 or report.get('mysql_visible_rows', 0) < 44:
        errors.append('fewer than 22 committed original turns')
    if report.get('history_contexts', 0) < 22 or report.get('model_contexts', 0) < 22:
        errors.append('missing actual context logs')
    if report.get('window_violations', 1):
        errors.append('context window violation')
    if report.get('profile') == 'default':
        if any(report.get(key, 0) for key in ('degrades','summary_triggers','summary_segments')):
            errors.append('default compressed history')
    elif report.get('profile') == 'demo':
        if any(report.get(key, 0) < 1 for key in ('degrades','summary_triggers','summary_starts','summary_segments')):
            errors.append('missing complete degradation/summary cascade')
        if not report.get('first_order_reference_ok') or not report.get('first_summary_injected'):
            errors.append('earliest order is not grounded in an injected summary')
        proof = report.get('nonblocking_timestamps', [])
        if not any(p.get('summary_blocked_until_after_reply') for p in proof):
            errors.append('missing controlled nonblocking proof')
    else:
        errors.append('unknown profile')
    return errors


def dialogue(turns):
    if turns < 22:
        raise ValueError('at least 22 turns required')
    first = '最开始请记住订单1001，我想查它的物流是否送到，原诉求只是查询签收与运输情况，不要帮我申请退款或建工单。'
    details = [
        '我白天要上课，无法一直盯着手机。若结果没有查到，请明确区分暂无记录和确实未发货；不要根据我的描述猜测物流节点，我希望以查询结果为准。',
        '这次只是做购买记录核对。我把包装放在宿舍，暂时无法拍照，不需要你联系商家或作处理承诺；如果状态已经更新，请说明当前能确认的事实。',
        '我需要安排收件时间，比较关心是否已送达和当前是否还有运输节点。不要把预计时间说成保证时间，也不要把已经签收等同于我已经检查过商品。',
        '之前咨询过别的订单，所以这次我会明确写订单号，避免搞混。我希望只查指定这单，不要沿用另一单的状态；若信息不足，就直接说明需要补充什么。',
    ]
    questions = [first + details[0]]
    for i in range(1, turns-1):
        order = str(1002 + (i % 2))
        question = f'现在查订单{order}的物流状态，告诉我目前能确认的结果。' + details[i % len(details)]
        question += '请把该订单号与对应查询结果一起说明。我没有提出更换收货地址，也没有确认退款；这条消息里提到的安排只是背景，实际处理仍然只做查询。'
        questions.append(question)
    questions.append('最开始那个订单后来怎么说？请按我最初查物流的诉求再查一次，告诉我现在能确认的结果。')
    return questions


def database_snapshot(conversation_id):
    from mewhelp.db.engine import SessionLocal
    with SessionLocal() as session:
        conv = session.get(Conversation, conversation_id)
        rows = session.scalars(select(Message).where(Message.conversation_id == conversation_id).order_by(Message.id)).all()
        segments = session.scalars(select(ConversationSummary).where(
            ConversationSummary.conversation_id == conversation_id).order_by(ConversationSummary.seq)).all()
        return {'S':conv.summary_upto_msg_id or 0, 'L':conv.layer1_from_msg_id or 0,
            'messages':[{'id':r.id,'role':r.role.value,'content':r.content} for r in rows],
            'segments':[{'id':r.id,'seq':r.seq,'from_msg_id':r.from_msg_id,'upto_msg_id':r.upto_msg_id,
                         'content':r.content} for r in segments]}


def verified_resume(path, *, questions, profile, session_id, user_id, snapshot):
    old = json.loads(path.read_text(encoding='utf-8'))
    if (old['profile'], old['session_id'], old['user_id']) != (profile, session_id, user_id):
        raise ValueError('resume identity or profile mismatch')
    done = [row for row in old['turns'] if row.get('result')]
    if len(snapshot['messages']) != 2 * len(done):
        raise ValueError('resume ledger contains unverified or extra turns')
    for index, row in enumerate(done):
        if (row['round'] != index + 1 or row['question'] != questions[index]
            or row['result']['stop_reason'] != 'completed'
            or snapshot['messages'][2 * index]['content'] != row['question']
            or snapshot['messages'][2 * index + 1]['content'] != row['result']['answer']):
            raise ValueError('resume original or frozen dialogue mismatch')
    return {'turns':done, 'conversation_id':old['conversation_id'],
            'resumed_from':str(path.resolve()),
            'prior_errors':[row['error'] for row in old['turns'] if row.get('error')]}


async def nonblocking_proof():
    from langchain_core.messages import AIMessage, HumanMessage
    with tempfile.TemporaryDirectory() as directory:
        engine = create_engine('sqlite:///' + (Path(directory)/'proof.sqlite').as_posix())
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, expire_on_commit=False)
        with factory.begin() as session:
            session.add(Conversation(id=1,session_id='controlled-proof',user_id='proof',layer1_from_msg_id=2))
            session.flush()
            session.add_all([Message(id=1,conversation_id=1,role=MsgRole.user,content='订单1001查物流'),
                             Message(id=2,conversation_id=1,role=MsgRole.assistant,content='尚未查到')])
        class Held:
            started = asyncio.Event()
            release = asyncio.Event()
            async def summarize(self, **kwargs):
                self.started.set()
                await self.release.wait()
                return SummaryResult('用户询问订单1001的物流进展。客服本批尚未查询到物流结果，用户提出的查询诉求仍未解决，目前没有可确认的派送结论。', {}, 1)
        held = Held()
        manager = SummaryTaskManager(factory, held, BudgetProfile())
        job = SummaryJob(1,0,2,(HistoryTurn('proof',1,2,(HumanMessage('订单1001查物流'),AIMessage('尚未查到'))),))
        start = time.perf_counter()
        assert manager.schedule(job)
        reply_at = time.perf_counter()
        await asyncio.wait_for(held.started.wait(), 2)
        blocked = not manager.inflight[1].done()
        held.release.set()
        await manager.aclose()
        engine.dispose()
        return {'kind':'controlled held summary, separate from real-upstream dialogue',
            'foreground_ms':round((reply_at-start)*1000,3),
            'summary_blocked_until_after_reply':blocked,
            'summary_complete_ms':round((time.perf_counter()-start)*1000,3)}


def collect_logs(path, conversation_id, session_id):
    histories, models, lifecycle = [], [], []
    for line in path.read_text(encoding='utf-8').splitlines():
        for label, target in [('history_ctx ',histories),('model_ctx ',models)]:
            if label in line:
                row = json.loads(line.split(label,1)[1])
                if row.get('conversation_id') == conversation_id and row.get('session_id') == session_id:
                    target.append(row)
        if (f'conversation={conversation_id}' in line and ('summary ' in line or '层1 降级' in line)
            and re.search(rf'conversation={conversation_id}(?:\s|$)',line)):
            lifecycle.append(line)
    return histories, models, lifecycle


def run(args):
    args.report_dir.mkdir(parents=True, exist_ok=False)
    raw_dir = args.raw_report_dir or Path('.cache/ch07/reports') / args.report_dir.name
    raw_dir.mkdir(parents=True, exist_ok=False)
    questions = dialogue(args.turns)
    session_id = args.session_prefix + '-' + args.profile
    report = {'profile':args.profile,'session_id':session_id,'user_id':args.user_id,'errors':[],
              'turns':[], 'raw_report_dir':str(raw_dir.resolve())}
    snapshots = []
    if args.resume_report:
        previous = json.loads(args.resume_report.read_text(encoding='utf-8'))
        snapshot = database_snapshot(previous['conversation_id'])
        report.update(verified_resume(args.resume_report, questions=questions, profile=args.profile,
            session_id=session_id, user_id=args.user_id, snapshot=snapshot))
        snapshots.append(snapshot)
    offset = len(report['turns'])
    with httpx.Client(base_url=args.base_url, timeout=200) as client:
        for i, question in enumerate(questions):
            if i < offset:
                continue
            started = dt.datetime.now(dt.UTC).isoformat()
            start = time.perf_counter()
            row = {'round':i+1,'question':question,'started_utc':started,'events':[]}
            try:
                with client.stream('POST','/ch05/chat/stream',json={'session_id':session_id,'user_id':args.user_id,'message':question}) as response:
                    response.raise_for_status()
                    name, data = '', []
                    for line in response.iter_lines():
                        if line.startswith('event:'): name = line[6:].strip()
                        elif line.startswith('data:'): data.append(line[5:].lstrip())
                        elif not line and data:
                            payload = json.loads('\n'.join(data))
                            row['events'].append({'event':name,'data':payload,'elapsed_ms':round((time.perf_counter()-start)*1000)})
                            if name == 'token' and 'first_token_ms' not in row:
                                row['first_token_ms'] = row['events'][-1]['elapsed_ms']
                            data = []
                terminal = row['events'][-1]
                if terminal['event'] != 'done':
                    raise ValueError('missing done or SSE error')
                result = terminal['data']
                if result.get('ledger_error') or result['stop_reason'] != 'completed':
                    raise ValueError('unsafe or bounded turn: '+ result['stop_reason'])
                row['result'] = result
                report['conversation_id'] = result['conversation_id']
                snapshots.append(database_snapshot(result['conversation_id']))
                row['done_ms'] = terminal['elapsed_ms']
                row['done_utc'] = dt.datetime.now(dt.UTC).isoformat()
            except Exception as error:  # noqa: BLE001 - actual HTTP failures are part of acceptance evidence
                row['error'] = f'{type(error).__name__}: {error}'
                report['errors'].append(row['error'])
            report['turns'].append(row)
            write_json(raw_dir/'http.json',report)
            print(f"{args.profile} round {i+1}: {row.get('error','done')} ({row.get('done_ms')} ms)",flush=True)
            if row.get('error'):
                break
    done = [r for r in report['turns'] if r.get('result')]
    report['turns_completed'] = len(done)
    report['real_http_sse'] = len(done) == args.turns
    report['real_usage'] = bool(done) and all(not r['result']['usage']['estimated'] and r['result']['usage']['input_tokens'] > 0 for r in done)
    if snapshots:
        snapshot = database_snapshot(report['conversation_id'])
        histories, models, lifecycle = collect_logs(args.log_path,report['conversation_id'],session_id)
        write_json(raw_dir/'database.json',snapshot)
        write_json(raw_dir/'contexts.json',{'history_ctx':histories,'model_ctx':models,'lifecycle':lifecycle})
        report.update(history_contexts=len(histories),model_contexts=len(models),
            degrades=sum('层1 降级' in x for x in lifecycle),
            summary_triggers=sum('summary trigger' in x for x in lifecycle),
            summary_starts=sum('summary start' in x for x in lifecycle), summary_segments=len(snapshot['segments']))
        visible = [m for m in snapshot['messages'] if m['role'] in ('user','assistant')]
        report['mysql_visible_rows'] = len(visible)
        report['mysql_originals_match'] = len(visible) == 2*len(done) and all(
            visible[2*i]['content'] == r['question'] and visible[2*i+1]['content'] == r['result']['answer'] for i,r in enumerate(done))
        report['immutable_segments'] = all(all(s in snapshot['segments'] for s in old['segments']) for old in snapshots)
        report['monotonic_anchors'] = all(a['S'] <= b['S'] and a['L'] <= b['L'] and b['S'] <= b['L'] for a,b in pairwise(snapshots))
        window = 18000 if args.profile == 'demo' else 128000
        output = 2000 if args.profile == 'demo' else 4096
        report['window_violations'] = sum(m['tokens_estimate']+output+900 > window for m in models)
        last = done[-1]['result']
        report['first_order_reference_ok'] = ('1001' in last['resolved_question'] and any(t['args'].get('order_id') == '1001' and t['ok'] for t in last['tool_trace']))
        final_histories = [h for h in histories if h['turn_id'] == models[-1]['turn_id']]
        report['first_summary_injected'] = bool(snapshot['segments'] and final_histories and any(
            s['id'] == snapshot['segments'][0]['id'] and '1001' in s['content'] for s in final_histories[-1]['summary_segments']))
        report['nonblocking_timestamps'] = [asyncio.run(nonblocking_proof())]
    report['validation_errors'] = grade_report(report)
    report['complete'] = not report['validation_errors']
    write_json(args.report_dir/'summary.json',{k:v for k,v in report.items() if k != 'turns'})
    write_json(raw_dir/'http.json',report)
    print(json.dumps({k:v for k,v in report.items() if k != 'turns'},ensure_ascii=False,indent=2))
    return 0 if report['complete'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',required=True)
    parser.add_argument('--profile',choices=['default','demo'],required=True)
    parser.add_argument('--report-dir',type=Path,required=True)
    parser.add_argument('--raw-report-dir',type=Path,help='Local raw evidence; defaults to ignored .cache/ch07/reports/<report name>')
    parser.add_argument('--resume-report',type=Path,help='Resume only verified committed originals from a preserved raw HTTP report')
    parser.add_argument('--user-id',required=True)
    parser.add_argument('--session-prefix',required=True)
    parser.add_argument('--turns',type=int,default=22)
    parser.add_argument('--log-path',type=Path,default=Path('log/app.log'))
    args = parser.parse_args()
    if not re.fullmatch(r'ch07-[a-z0-9-]{1,30}', args.session_prefix):
        parser.error('use a unique ch07- session prefix')
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
