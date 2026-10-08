"""Frozen labeled prompt checks with actual provider output and usage."""

import argparse
import asyncio
import hashlib
import json
import time
from pathlib import Path

from mewhelp.ch05.config import get_ch05_model
from mewhelp.ch05.evidence import EvidenceEnvelope
from mewhelp.ch05.limits import AgentLimits
from mewhelp.ch05.state import WorkflowContext
from mewhelp.config import get_settings
from mewhelp.knowledge.answering import source_dtos
from mewhelp.knowledge.filters import SearchFilters
from mewhelp.knowledge.retrieval import RankedChunk, RetrievalResult
from mewhelp.knowledge.store import KnowledgeChunk, snapshot_chunk

from .generation import KNOWLEDGE_ANSWER_SYSTEM, generate_knowledge_answer
from .snapshots import snapshot_result


def load_generation_samples(path):
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    seen = set()
    for row in rows:
        if row['id'] in seen:
            raise ValueError('duplicate prompt sample id')
        seen.add(row['id'])
        if (type(row.get('answerable')) is not bool or not row.get('question','').strip()
            or not row.get('label_basis','').strip() or not row.get('evidence')
            or any(not item.get('question') or not item.get('answer') for item in row['evidence'])):
            raise ValueError('invalid labeled generation sample')
    if len(rows) < 12:
        raise ValueError('at least12 labeled generation samples required')
    return rows


async def run_generation_samples(samples, output, *, model_factory=get_ch05_model):
    rows = load_generation_samples(samples)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise ValueError('use a new output path; never overwrite actual prompt evidence')
    manifest = {'kind':'generation_prompt_validation_only', 'sample_count':len(rows),
        'dataset_hash':hashlib.sha256(samples.read_bytes()).hexdigest(),
        'prompt_hash':hashlib.sha256(KNOWLEDGE_ANSWER_SYSTEM.encode()).hexdigest(),
        'model':get_settings().llm_model, 'labels_frozen_before_run':True}
    output.with_suffix('.manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    failures = 0
    with output.open('x', encoding='utf-8') as target:
        for sample in rows:
            captured = {}
            def factory(*args, record=captured, **kwargs):
                base = model_factory(*args, **kwargs)
                class Proxy:
                    model_name = getattr(base, 'model_name', manifest['model'])
                    def with_structured_output(self, schema, **options):
                        upstream = base.with_structured_output(schema, **options)
                        class Capture:
                            async def ainvoke(self, messages):
                                record['messages'] = [m.model_dump(mode='json') for m in messages]
                                envelope = await upstream.ainvoke(messages)
                                record.update(envelope)
                                return envelope
                        return Capture()
                return Proxy()
            chunks = [snapshot_chunk(KnowledgeChunk(id=i,category='标注样例',questions=item['question'],
                answer=item['answer'],section_path=f"样例/{sample['id']}/{i}",content_type='faq',is_key_clause=False))
                for i,item in enumerate(sample['evidence'],1)]
            # Fixed labeled evidence exercises generation only; no artificial score claims retrieval quality.
            evidence = RetrievalResult(chunks, [RankedChunk(c,None) for c in chunks])
            snapshot = snapshot_result(evidence,query=sample['question'],filters=SearchFilters())
            envelope = EvidenceEnvelope(sources=source_dtos(evidence),scores=[None]*len(chunks),
                threshold=0.,context_budget=32000,retrieved_chunks=snapshot.model_dump(mode='json'))
            ctx = WorkflowContext(None,factory,None,AgentLimits())
            state = {'question':sample['question'],'resolved_question':sample['question'],'route':'knowledge',
                'evidence':envelope.model_dump(),'gate':{'passed':True},'entry_point':'cli',
                'started_at':time.time(),'usage':{},'calls':{}}
            started = time.perf_counter()
            try:
                result = await generate_knowledge_answer(state,ctx,lambda x:None,record_pool=False)
                parsed = captured.get('parsed')
                parsed_json = parsed.model_dump(mode='json') if parsed is not None else None
                passed = (bool(parsed_json) and parsed_json.get('answerable') == sample['answerable']
                    and result.get('refused') == (not sample['answerable'])
                    and all(t in result['answer'] for t in sample.get('must_include',[]))
                    and all(t not in result['answer'] for t in sample.get('must_not_include',[])))
                error = None
            except Exception as exc:  # noqa: BLE001 — each actual failure is saved, then exits nonzero
                result, parsed_json, passed, error = None,None,False,f'{type(exc).__name__}: {exc}'
            raw = captured.get('raw')
            row = {'id':sample['id'],'expected':sample,'parsed':parsed_json,'result':result,
                'raw_message':raw.model_dump(mode='json') if raw is not None else None,
                'parsing_error':str(captured.get('parsing_error') or ''),'messages':captured.get('messages'),
                'elapsed_ms':round((time.perf_counter()-started)*1000),'passed':bool(passed),'error':error}
            target.write(json.dumps(row,ensure_ascii=False,default=str)+'\n'); target.flush()
            failures += not passed
            print(json.dumps({'id':sample['id'],'passed':bool(passed),'error':error},ensure_ascii=False),flush=True)
    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--suite',choices=['generation'],required=True)
    parser.add_argument('--samples',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    raise SystemExit(1 if asyncio.run(run_generation_samples(args.samples,args.output)) else 0)


if __name__ == '__main__':
    main()
