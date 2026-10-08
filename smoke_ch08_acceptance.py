"""本机真实六项验收；只重启已核对属于本章的物流进程。"""
import argparse
import asyncio
import hashlib
import json
import os
import signal
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from sqlalchemy import func, select, text

from mewhelp.ch08.evaluation import validate_acceptance
from mewhelp.db.engine import SessionLocal, engine
from mewhelp.db.models import Conversation, Ticket, ToolAuditLog

ROOT = Path(__file__).resolve().parent


def listener(port):
    command = f"Get-NetTCPConnection -State Listen -LocalPort {int(port)} | ForEach-Object {{ Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.OwningProcess) }} | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress"
    result = subprocess.run(['pwsh','-NoProfile','-Command',command],check=True,capture_output=True,text=True,encoding='utf-8')
    row = json.loads(result.stdout)
    if isinstance(row,list):
        if len(row) != 1:
            raise RuntimeError('端口所属进程不唯一')
        row=row[0]
    return row


def code_hash():
    digest=hashlib.sha256()
    for path in sorted((ROOT/'src/mewhelp').rglob('*')):
        if path.suffix in ('.py','.html') and 'mcp_servers' not in path.parts:
            digest.update(str(path.relative_to(ROOT)).encode());digest.update(path.read_bytes())
    return digest.hexdigest()


def launch_extension(extension, logfile):
    with logfile.open('w',encoding='utf-8') as log:
        return subprocess.Popen([str(ROOT/'.venv-ch03/Scripts/python.exe'),'-X','utf8',str(extension.resolve())],cwd=ROOT,
            stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW).pid


def audits(session_id):
    with SessionLocal() as db:
        cid=db.scalar(select(Conversation.id).where(Conversation.session_id==session_id))
        rows=db.scalars(select(ToolAuditLog).where(ToolAuditLog.conversation_id==cid).order_by(ToolAuditLog.id)).all()
        return [{column.name:getattr(row,column.name) for column in ToolAuditLog.__table__.columns} for row in rows]


def ticket_count(session_id):
    with SessionLocal() as db:
        cid=db.scalar(select(Conversation.id).where(Conversation.session_id==session_id))
        return db.scalar(select(func.count()).select_from(Ticket).where(Ticket.conversation_id==cid))


def grounded_business_answer(result):
    """核对答案至少包含所查对象与结果状态，原始字段留报告供人工核对。"""
    for trace in result['tool_trace']:
        if not trace['ok'] or trace['name'] not in ('query_logistics','query_warranty','query_return_progress'):
            continue
        try:
            data=json.loads(trace['content'])
        except (TypeError,ValueError):
            continue
        identity=data.get('order_id') or data.get('return_id')
        status=data.get('status')
        answer=result['answer']
        aliases={'在保':('在保','保修期内'),'已过保':('已过保','过了保修','保修期外'),
            '已签收':('已签收','已经签收','签收完成'),'运输中':('运输中','运输途中'),
            '待发货':('待发货','尚未发货','还未发货'),
            '退款已完成':('退款已完成','已完成退款'),'退货已入库':('退货已入库','已收到退货'),
            '申请已收到':('申请已收到','已收到申请','申请已受理')}
        if identity and str(identity) in answer and any(value in answer for value in aliases.get(status,(status or '不存在的状态',))):
            return True
    return False


def database_probe():
    with engine.connect() as connection:
        alive=connection.execute(text('SELECT 1')).scalar()
        ddl=connection.execute(text('SHOW CREATE TABLE tool_audit_logs')).one()[1]
    if alive != 1 or 'FOREIGN KEY' in ddl or 'utf8mb4' not in ddl or any(value not in ddl for value in ('成功','失败','超时','校验拦下','权限拒绝','idx_conversation_id','idx_tool_name','idx_status')):
        raise RuntimeError('真实MySQL审计DDL不满足合同')
    return {'select_one':alive,'show_create_table':ddl}


async def write_timeout(outdir):
    """隔离故障处理器；复用真实确认校验/引擎/审计，绝不挂到默认服务。"""
    from langchain_core.tools import tool

    from mewhelp.ch08.confirmation import make_ticket_preview, verify_ticket_confirmation
    from mewhelp.ch08.schemas import TicketResumeRequest
    from mewhelp.tools.audit import ToolAuditWriter
    from mewhelp.tools.contracts import ToolCallContext
    from mewhelp.tools.engine import ToolExecutionEngine
    from mewhelp.tools.registry import ToolRegistry, ToolSpec
    calls=[]
    @tool
    async def create_ticket(description:str,ticket_type:str)->str:
        """隔离写超时验收，不接真实写库处理器。"""
        calls.append(description);await asyncio.sleep(1);return '不会到达'
    sid='ch08-write-timeout-'+uuid4().hex
    with SessionLocal() as db:
        owner=Conversation(session_id=sid,user_id='ch08-acceptance');db.add(owner);db.flush();cid=owner.id;db.commit()
    writer=ToolAuditWriter(SessionLocal)
    execution=ToolExecutionEngine(writer)
    snapshot=ToolRegistry({'create_ticket':ToolSpec(create_ticket,permission='write',retryable=True,timeout_seconds=.04)}).snapshot()
    args={'description':'键盘坏了','ticket_type':'售后'}
    context=ToolCallContext(cid,sid,'ch08-acceptance',tool_call_id=uuid4().hex,
        intent_evidence={'explicit_request':True,'utterances':['键盘坏了，帮我建售后工单']})
    # Use the actual request-evidence producer, not a fabricated approval flag.
    from mewhelp.ch08.ticket_intent import ticket_request_patch
    context=replace(context,intent_evidence=ticket_request_patch('键盘坏了，帮我建售后工单','fault-user',None))
    prepared=await execution.prepare(snapshot,'create_ticket',args,context)
    preview=make_ticket_preview(prepared,context)
    auth=verify_ticket_confirmation({'ticket_preview':preview,'ticket_status':'pending',
        'session_id':sid,'user_id':'ch08-acceptance','conversation_id':cid},
        TicketResumeRequest(session_id=sid,user_id='ch08-acceptance',confirmation_id=preview['confirmation_id'],action='confirm'),snapshot)
    result=await execution.execute(snapshot,'create_ticket',args,replace(context,authorization=auth))
    writer.close()
    if len(calls)!=1 or result.retry_count!=0:
        raise RuntimeError('写超时发生自动重试')
    return audits(sid),result.content


async def run(base_url,outdir,browser_report=None,expected_logistics_pid=None,business_evidence=None):
    outdir.mkdir(parents=True,exist_ok=False)
    evidence=[];responses=[];run_errors=[]
    probe=database_probe()
    customer_port=urlparse(base_url).port or 80
    baseline=listener(customer_port);baseline_hash=code_hash()
    old=listener(9021)
    if expected_logistics_pid is None or old['ProcessId'] != expected_logistics_pid or 'mewhelp.ch08.mcp_servers.logistics' not in old['CommandLine']:
        raise RuntimeError('物流PID与显式指定的本章进程不一致，拒绝关闭')
    plugins=ROOT/'tool_plugins';plugins.mkdir(exist_ok=True)
    plugin=plugins/'demo_ch08_acceptance.py'
    if plugin.exists():
        raise RuntimeError('验收插件已存在，避免覆盖用户文件')
    config_path=ROOT/'config/ch08-tools.json';original_config=config_path.read_bytes()
    async with httpx.AsyncClient(base_url=base_url,timeout=240) as client:
        async def turn(question,label,session=None):
            sid=session or 'ch08-'+label+'-'+uuid4().hex[:12]
            response=await client.post('/ch05/agent',json={'session_id':sid,'user_id':'ch08-acceptance','message':question})
            response.raise_for_status();result=response.json();responses.append({'question':question,'actual':result})
            (outdir/'responses.json').write_text(json.dumps(responses,ensure_ascii=False,indent=2),encoding='utf-8')
            return result
        try:
            plugin.write_text('from langchain_core.tools import tool\nfrom mewhelp.tools.contracts import ToolSpec\n@tool\ndef query_demo_marker(marker:str)->dict:\n    """查询用户给定短语的客服演示标记，用于验证工具热注册。"""\n    return {"marker":marker,"registration":"工具热注册成功"}\n@tool\nasync def query_timeout_demo(marker:str)->str:\n    """查询超时演示，仅用于人工测试暂时性故障兜底。"""\n    import asyncio\n    await asyncio.sleep(1)\n    return marker\ndef register(registry):\n    registry.register(ToolSpec(query_demo_marker))\n    registry.register(ToolSpec(query_timeout_demo,timeout_seconds=.04))\n',encoding='utf-8')
            first=await turn('请用演示标记查询工具回显“工具热注册成功”','registered')
            evidence.append({'requirement':1,'tool_used':any(t['name']=='query_demo_marker' and t['ok'] for t in first['tool_trace']),
                'registration_only':code_hash()==baseline_hash,'audits':audits(first['session_id'])})
            if business_evidence:
                prior=json.loads(business_evidence.read_text(encoding='utf-8'))
                row=next(item for item in prior['requirements'] if item['requirement']==2)
                evidence.append({**row,'evidence_source':str(business_evidence)})
            else:
                business=[]
                for question in ('查询订单1001的物流轨迹','查询订单1001机械键盘是否在保','查询退货单R1001的退货进度'):
                    business.append(await turn(question,'mcp'))
                business_audits=[row for result in business for row in audits(result['session_id'])]
                evidence.append({'requirement':2,'servers':sorted({a['mcp_server'] for a in business_audits if a['tool_source']=='mcp'}),
                    'answers_grounded':all(grounded_business_answer(r) for r in business),'audits':business_audits})
            if listener(9021) != old:
                raise RuntimeError('物流进程在验收期间已改变，拒绝关闭')
            extension=outdir/'logistics_extended.py'
            extension.write_text('from mewhelp.ch08.mcp_servers.logistics import build_server\nserver=build_server(host="127.0.0.1",port=9021)\n@server.tool()\ndef query_delivery_window(order_id:str)->dict:\n    """查询指定订单的配送客服咨询窗口时间，仅返回mock数据。"""\n    return {"order_id":order_id,"service_window":"09:00至18:00","channel":"配送客服"}\nserver.run(transport="streamable-http")\n',encoding='utf-8')
            os.kill(old['ProcessId'],signal.SIGTERM)
            await asyncio.to_thread(launch_extension, extension, outdir/'extended-server.log')
            deadline=time.monotonic()+20
            while True:
                try:
                    current=listener(9021)
                    if current['ProcessId']!=old['ProcessId']:break
                except (subprocess.CalledProcessError,json.JSONDecodeError):
                    pass
                if time.monotonic()>deadline:raise TimeoutError('新物流进程未监听')
                await asyncio.sleep(.2)
            config=json.loads(original_config)
            config['permissions']['logistics']['query_delivery_window']={'mode':'readonly'}
            config_path.write_text(json.dumps(config,ensure_ascii=False,indent=2),encoding='utf-8')
            hot=await turn('查询订单1001的配送客服咨询窗口时间','hot-mcp')
            evidence.append({'requirement':3,'tool_used':any(t['name']=='query_delivery_window' and t['ok'] for t in hot['tool_trace']),
                'customer_pid_unchanged':listener(customer_port)['ProcessId']==baseline['ProcessId'],'customer_code_unchanged':code_hash()==baseline_hash,
                'server_restarted':current['ProcessId']!=old['ProcessId'],'audits':audits(hot['session_id'])})
            if browser_report:
                evidence.extend(json.loads(browser_report.read_text(encoding='utf-8'))['requirements'])
            else:
                missing=await turn('帮我建个工单','confirm')
                waiting=await turn('键盘坏了，请帮我建售后工单','confirm',missing['session_id'])
                before=ticket_count(waiting['session_id'])
                confirmed=await client.post('/ch08/tickets/resume',json={'session_id':waiting['session_id'],'user_id':'ch08-acceptance',
                    'confirmation_id':waiting['ticket_preview']['confirmation_id'],'action':'confirm'})
                confirmed.raise_for_status();result=confirmed.json();responses.append({'confirm':result})
                evidence.append({'requirement':4,'clarified':missing['stop_reason']=='clarification','preview':bool(waiting['ticket_preview']),
                    'tickets_delta':ticket_count(waiting['session_id'])-before,'ticket_no':result['ticket_receipt']['ticket_no'],
                    'answer':result['answer'],'audits':audits(waiting['session_id'])})
                cancelled=await turn('键盘坏了，请帮我建售后工单','cancel');before=ticket_count(cancelled['session_id'])
                response=await client.post('/ch08/tickets/resume',json={'session_id':cancelled['session_id'],'user_id':'ch08-acceptance',
                    'confirmation_id':cancelled['ticket_preview']['confirmation_id'],'action':'cancel'})
                response.raise_for_status()
                evidence.append({'requirement':5,'preview':bool(cancelled['ticket_preview']),'tickets_delta':ticket_count(cancelled['session_id'])-before,
                    'audits':audits(cancelled['session_id'])})
            delayed=await turn('请调用超时演示工具查询 marker=test 的结果','read-timeout')
            writing,write_message=await write_timeout(outdir)
            evidence.append({'requirement':6,'honest_failure':not any(t['ok'] for t in delayed['tool_trace']) and
                ('超时' in delayed['answer'] or '失败' in delayed['answer']) and '结果未知' in write_message,
                'audits':audits(delayed['session_id'])+writing})
        except Exception as exc:  # noqa: BLE001 — 失败验收必须留报告，不能把已做证据丢失
            run_errors.append(f'{type(exc).__name__}: {exc}')
        finally:
            config_path.write_bytes(original_config)
            plugin.unlink(missing_ok=True)
    summary=validate_acceptance(evidence)
    if run_errors:
        summary['passed']=False
        summary['failures'].extend(run_errors)
    summary['ticket_verification']='browser' if browser_report else 'HTTP only; browser permission pending'
    (outdir/'evidence.json').write_text(json.dumps({'database':probe,'requirements':evidence,'responses':responses},ensure_ascii=False,default=str,indent=2),encoding='utf-8')
    (outdir/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False));return 0 if summary['passed'] else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--base-url',default='http://127.0.0.1:9020')
    parser.add_argument('--outdir',type=Path,required=True);parser.add_argument('--browser-report',type=Path)
    parser.add_argument('--probe-only',action='store_true')
    parser.add_argument('--expected-logistics-pid',type=int,help='明确指定本次允许重启的标准物流进程PID')
    parser.add_argument('--business-evidence',type=Path,help='沿用未受修复影响的三项MCP业务查询真实证据')
    args=parser.parse_args()
    if args.probe_only:
        args.outdir.mkdir(parents=True,exist_ok=True)
        (args.outdir/'database.json').write_text(json.dumps(database_probe(),ensure_ascii=False,indent=2),encoding='utf-8')
        print('MySQL SELECT 1 / 中文ENUM / 无外键 / utf8mb4 / 三个索引核对通过')
    else:
        raise SystemExit(asyncio.run(run(args.base_url,args.outdir,args.browser_report,args.expected_logistics_pid,args.business_evidence)))
