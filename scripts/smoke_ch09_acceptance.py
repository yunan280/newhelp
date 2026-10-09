"""Export existing, real Ch09 acceptance evidence; never replay model requests."""
import argparse
import datetime as dt
import json
from pathlib import Path

import httpx
from dotenv import dotenv_values
from langfuse import Langfuse
from sqlalchemy import select

from mewhelp.db.engine import SessionLocal
from mewhelp.db.models import Conversation, Message, ToolAuditLog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:9030')
    parser.add_argument('--conversation-id', type=int, action='append', default=[])
    parser.add_argument('--review-id', type=int, action='append', default=[])
    parser.add_argument('--trace-id', action='append', default=[])
    parser.add_argument('--from-utc', required=True)
    parser.add_argument('--to-utc', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    origin = httpx.URL(args.base_url)
    if (origin.scheme != 'http' or origin.host not in {'127.0.0.1', 'localhost'}
            or origin.userinfo or origin.query or origin.fragment or origin.path != '/'):
        parser.error('Use an explicit local service origin')
    report = {'captured_at': dt.datetime.now(dt.UTC).isoformat(),
              'kind': 'read-only actual evidence, no requests replayed',
              'base_url': args.base_url, 'conversations': [], 'reviews': [], 'traces': []}
    with httpx.Client(base_url=args.base_url, timeout=60) as http:
        def read(path, **params):
            response = http.get(path, params=params)
            response.raise_for_status()
            return response.json()
        report['flywheel'] = read('/api/ch09/flywheel/status')
        report['eval_runs'] = read('/api/ch09/eval-runs')
        report['trends'] = read('/api/ch09/eval-trends')
        report['token_costs'] = read('/api/ch09/token-costs',
                                    **{'from': args.from_utc, 'to': args.to_utc})
        for review_id in args.review_id:
            report['reviews'].append(read(f'/api/ch09/reviews/{review_id}', page_size=100))
    with SessionLocal() as session:
        for conversation_id in args.conversation_id:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise ValueError(f'Missing conversation {conversation_id}')
            messages = session.scalars(select(Message).where(
                Message.conversation_id == conversation_id).order_by(Message.id)).all()
            audits = session.scalars(select(ToolAuditLog).where(
                ToolAuditLog.conversation_id == conversation_id).order_by(ToolAuditLog.id)).all()
            report['conversations'].append({
                'id': str(conversation_id), 'session_id': conversation.session_id,
                'messages': [{'id': str(m.id), 'role': m.role.value, 'content': m.content,
                              'retrieval_snapshot': m.retrieval_snapshot} for m in messages],
                'audits': [{column.name: getattr(a, column.name)
                            for column in ToolAuditLog.__table__.columns} for a in audits]})
    private = dotenv_values('.env.ch09.langfuse')
    client = Langfuse(base_url=private['LANGFUSE_BASE_URL'],
                      public_key=private['LANGFUSE_PUBLIC_KEY'],
                      secret_key=private['LANGFUSE_SECRET_KEY'])
    try:
        for trace_id in args.trace_id:
            observations, cursor = [], None
            while True:
                page = client.api.observations.get_many(trace_id=trace_id, limit=100,
                    cursor=cursor, fields='core,basic,time,metadata,io,model,usage')
                observations.extend(row.model_dump(mode='json') for row in page.data)
                cursor = page.meta.cursor
                if not cursor:
                    break
            if not observations:
                raise ValueError(f'No exported observations for trace {trace_id}')
            report['traces'].append({'trace_id': trace_id,
                'url': client.get_trace_url(trace_id=trace_id),
                'observations': observations})
    finally:
        client.shutdown()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                           encoding='utf-8')
    print(json.dumps({'output': str(args.output.resolve()),
                      'conversations': len(report['conversations']),
                      'traces': len(report['traces'])}, ensure_ascii=False))


if __name__ == '__main__':
    main()
