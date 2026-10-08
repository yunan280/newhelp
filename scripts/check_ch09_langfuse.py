"""Verify private project and real export/readback; this is a synthetic deployment probe."""
import argparse
import json
import time
from pathlib import Path

from dotenv import dotenv_values

from mewhelp.ch09.config import Ch09Settings
from mewhelp.ch09.contracts import RequestTraceContext
from mewhelp.ch09.observability import ObservationRuntime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    env = dotenv_values(root / '.env.ch09.langfuse')
    settings = Ch09Settings(enabled=True, base_url=env['LANGFUSE_BASE_URL'],
                            public_key=env['LANGFUSE_PUBLIC_KEY'],
                            secret_key=env['LANGFUSE_SECRET_KEY'])
    runtime = ObservationRuntime(settings=settings)
    try:
        assert runtime.client.auth_check(), 'local project credentials are invalid'
        projects = runtime.client.api.projects.get()
        with runtime.request(RequestTraceContext(entry_point='deployment_probe',
                                                 trace_kind='evaluation'),
                             input={'synthetic_deployment_probe': True}) as request:
            request.set_intent('部署探针')
            with runtime.observe('export_readback', input={'sample': '中文原文'}) as span:
                span.update(output={'sample': '中文返回'})
            request.finish(status='completed', output={'synthetic_deployment_probe': True})
        runtime.flush()
        for attempt in range(20):
            response = runtime.client.api.observations.get_many(
                trace_id=request.trace_id, fields='core,basic,time,metadata,io', limit=100)
            if len(response.data) >= 2:
                break
            time.sleep(2)
        else:
            raise RuntimeError('Langfuse readback did not contain both completed observations')
        evidence = {'base_url': settings.base_url, 'auth_check': True,
                    'projects': [p.name for p in projects.data], 'trace_id': request.trace_id,
                    'probe_kind': 'synthetic_deployment_only',
                    'observations': [s.model_dump(mode='json') for s in response.data]}
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2),
                                   encoding='utf-8')
        print(json.dumps({'auth_check': True, 'trace_id': request.trace_id,
                          'observations': len(response.data), 'probe_kind': evidence['probe_kind']},
                         ensure_ascii=False))
    finally:
        runtime.shutdown()


if __name__ == '__main__':
    main()
