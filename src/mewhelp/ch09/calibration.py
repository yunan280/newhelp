"""Twenty original-query calibration cases; no test labels influence selection."""

import argparse
import asyncio
import json
import math
import re
from dataclasses import asdict
from pathlib import Path

from mewhelp.ch05.evidence import retrieve_current_evidence
from mewhelp.knowledge.evaluation.dataset import load_dataset
from mewhelp.knowledge.evaluation.runner import _factory, _new_index, _snapshots
from mewhelp.knowledge.retrieval import RetrievalRuntime
from mewhelp.knowledge.store import put_chunk
from mewhelp.knowledge.sync import reindex_all
from mewhelp.knowledge.vectors import MilvusSettings

from .confidence import ConfidenceProfile, current_profile_identity, score_evidence, validate_scores


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def choose_profile(cases, observations, *, model_metadata, dataset_hashes, retrieval_config_hash):
    selected = [case for case in cases if case.split == 'calibration']
    if (len(selected) != 20 or len({c.id for c in selected}) != 20
        or set(observations) != {c.id for c in selected}):
        raise ValueError('only complete unique 20 calibration observations may select parameters')
    for scores in observations.values():
        validate_scores(scores)
    cutoffs = sorted({score for scores in observations.values() for score in scores[:5]})
    if not cutoffs:
        raise ValueError('no observed scores available for calibration')
    weights = [(s / 4, n / 4, (4 - s - n) / 4) for s in range(2, 5) for n in range(5 - s)]
    candidates = []
    for cutoff in cutoffs:
        for weight in weights:
            base = ConfidenceProfile(5, cutoff, weight, 0., model_metadata, dataset_hashes,
                                     retrieval_config_hash)
            features = {key: score_evidence(scores, profile=base) for key, scores in observations.items()}
            values = [feature.value for feature in features.values()]
            thresholds = sorted({*values, math.nextafter(max(values), math.inf)})
            for threshold in thresholds:
                allowed = {key for key, feature in features.items() if feature.top1 is not None
                           and feature.value >= threshold}
                false_allow = [c.id for c in selected if c.should_refuse and c.id in allowed]
                false_refuse = [c.id for c in selected if not c.should_refuse and c.id not in allowed]
                candidates.append({'effective_cutoff': cutoff, 'weights': weight, 'threshold': threshold,
                                       'false_allow': len(false_allow), 'false_refuse': len(false_refuse),
                                       'false_allow_ids': false_allow, 'false_refuse_ids': false_refuse,
                                       'allowed_count': len(allowed)})
    best = min(candidates, key=lambda c: (c['false_allow'], c['false_refuse'], -c['threshold'],
                                         c['effective_cutoff'], c['weights']))
    profile = ConfidenceProfile(5, best['effective_cutoff'], best['weights'], best['threshold'],
                                model_metadata, dataset_hashes, retrieval_config_hash)
    return profile, dict(sample_count=20, **best, candidates=candidates)


def validate_isolation(workdir, run_id, collection, online_collection, *, prefix='ch09_conf_'):
    if (not re.fullmatch('[a-z][a-z0-9_]{0,63}', run_id) or collection != prefix + run_id
        or collection == online_collection or not Path(workdir).name):
        raise ValueError('calibration/evaluation must use an isolated named collection and SQLite')


async def prepare_isolated_corpus(*, dataset, workdir, run_id, retrieval_runtime=None,
                                  prefix='ch09_conf_'):
    collection = prefix + run_id
    validate_isolation(workdir, run_id, collection, MilvusSettings().milvus_collection, prefix=prefix)
    corpus, cases = load_dataset(dataset / 'corpus.jsonl', dataset / 'queries.jsonl')
    # A fresh directory prevents cache reuse from silently changing frozen input or labels.
    workdir.mkdir(parents=True, exist_ok=False)
    factory, index = _factory(workdir), _new_index(collection)
    await asyncio.to_thread(index.ensure_collection)
    with factory() as session:
        for draft in corpus:
            put_chunk(session, draft)
        session.commit()
    if retrieval_runtime is None:
        from mewhelp.knowledge.embedding import embed_texts
        from mewhelp.knowledge.reranking import rerank_chunks
        embed, rerank = embed_texts, rerank_chunks
    else:
        embed, rerank = retrieval_runtime.embed, retrieval_runtime.rerank
    await asyncio.to_thread(reindex_all, factory, embed, index)
    issues = await asyncio.to_thread(index.audit, _snapshots(factory))
    if issues:
        write_json(workdir / 'audit-errors.json', issues)
        raise RuntimeError('isolated frozen index audit failed')
    return RetrievalRuntime(factory, embed, index, rerank), cases, collection


async def calibrate_current_path(*, dataset: Path, workdir: Path, run_id: str,
                                 retrieval_runtime: RetrievalRuntime | None = None) -> Path:
    identity = await asyncio.to_thread(current_profile_identity, dataset)
    runtime, cases, collection = await prepare_isolated_corpus(
        dataset=dataset, workdir=workdir, run_id=run_id, retrieval_runtime=retrieval_runtime)
    observations, records = {}, []
    write_json(workdir / 'manifest.json', dict(run_id=run_id, collection=collection,
               sqlite=str((workdir / 'source.sqlite3').resolve()), calibration_count=20,
               test_count=0, **identity))
    from types import SimpleNamespace
    rag = SimpleNamespace(retrieval=runtime)
    for case in [c for c in cases if c.split == 'calibration']:
        evidence = await asyncio.to_thread(retrieve_current_evidence, case.question, rag=rag, filters=case.filters)
        observations[case.id] = [r.score for r in evidence.final]
        records.append({'id': case.id, 'original_question': case.question, 'should_refuse': case.should_refuse,
                            'filters': case.filters.model_dump(), 'candidates': [asdict(c) for c in evidence.candidates],
                            'final': [asdict(r) for r in evidence.final]})
        write_json(workdir / 'observations.json', records)
        print(f'calibration {case.id}: {[r.score for r in evidence.final[:5]]}', flush=True)
    profile, report = choose_profile(cases, observations, **{k: v for k, v in identity.items() if k != 'top_k'})
    for record in records:
        record['confidence'] = asdict(score_evidence(observations[record['id']], profile=profile))
    write_json(workdir / 'observations.json', records)
    write_json(workdir / 'search.json', report)
    path = workdir / 'confidence.json'
    write_json(path, asdict(profile))
    print(json.dumps({k: v for k, v in report.items() if k != 'candidates'}, ensure_ascii=False), flush=True)
    return path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--workdir', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    args = parser.parse_args()
    asyncio.run(calibrate_current_path(dataset=args.dataset, workdir=args.workdir, run_id=args.run_id))


if __name__ == '__main__':
    main()
