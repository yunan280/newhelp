from dataclasses import asdict, replace
from pathlib import Path

import pytest

from mewhelp.ch05.evidence import retrieve_current_evidence
from mewhelp.ch09.calibration import choose_profile, validate_isolation
from mewhelp.knowledge.evaluation.dataset import load_dataset
from mewhelp.knowledge.filters import SearchFilters
from mewhelp.knowledge.retrieval import RetrievalResult

DATASET = Path(__file__).resolve().parents[2] / 'eval/ch04'


def cases():
    return load_dataset(DATASET / 'corpus.jsonl', DATASET / 'queries.jsonl')[1]


def choose(items, scores):
    return choose_profile(items, scores, model_metadata={'revision': 'actual-revision'},
                          dataset_hashes={'corpus_hash': 'a' * 64, 'query_hash': 'b' * 64},
                          retrieval_config_hash='c' * 64)


def test_test_labels_never_enter_candidate_search():
    items = cases()
    observations = {case.id: [.8, .6] if not case.should_refuse else [.2]
                    for case in items if case.split == 'calibration'}
    first, report = choose(items, observations)
    mutated = [replace(c, should_refuse=not c.should_refuse) if c.split == 'test' else c for c in items]
    second, repeated = choose(mutated, observations)
    assert first == second and report == repeated
    assert report['sample_count'] == 20 and report['false_allow'] == 0
    assert report['false_refuse'] == 0 and report['candidates']


def test_all_refusal_candidate_is_reported_without_threshold_fudging():
    items = [replace(c, should_refuse=True) if c.split == 'calibration' else c for c in cases()]
    selected, report = choose(items, {c.id: [.9] for c in items if c.split == 'calibration'})
    assert selected.threshold > .9 and report['allowed_count'] == 0
    assert report['false_allow'] == report['false_refuse'] == 0


def test_incomplete_or_test_observations_are_rejected():
    items = cases()
    with pytest.raises(ValueError, match='20 calibration'):
        choose(items, {c.id: [.8] for c in items})
    with pytest.raises(ValueError, match='20 calibration'):
        choose(items, {})


def test_eval_index_can_never_alias_online_collection(tmp_path):
    validate_isolation(tmp_path, 'ch09_cal_test', 'ch09_conf_ch09_cal_test', 'knowledge')
    with pytest.raises(ValueError, match='isolated'):
        validate_isolation(tmp_path, 'ch09_cal_test', 'knowledge', 'knowledge')
    with pytest.raises(ValueError, match='isolated'):
        validate_isolation(tmp_path, '../bad', 'ch09_conf_../bad', 'knowledge')


def test_current_retrieval_shares_raw_original_query(monkeypatch):
    from types import SimpleNamespace
    captured = []
    raw = RetrievalResult([], [])
    def retrieve(runtime, query, filters):
        captured.append((runtime, asdict(query), filters))
        return raw
    monkeypatch.setattr('mewhelp.ch05.evidence.retrieve_evidence', retrieve)
    rag = SimpleNamespace(retrieval=object())
    filters = SearchFilters()
    original = '我这单到底咋退？'
    assert retrieve_current_evidence(original, rag=rag, filters=filters) is raw
    assert captured[0][1]['original'] == captured[0][1]['canonical'] == captured[0][1]['bm25_query'] == original


@pytest.mark.asyncio
async def test_online_envelope_preserves_exact_raw_rank_and_text(monkeypatch):
    from types import SimpleNamespace

    from mewhelp.ch05.evidence import retrieve_knowledge
    from mewhelp.knowledge.retrieval import RankedChunk
    from mewhelp.knowledge.store import ChunkSnapshot
    chunk = ChunkSnapshot(1, '原文完整中文', '问题', '答案', '路径', '分类', None, 'faq', False, 'a' * 64)
    raw = RetrievalResult([chunk], [RankedChunk(chunk, .9)])
    monkeypatch.setattr('mewhelp.ch05.evidence.retrieve_evidence', lambda *a: raw)
    rag = SimpleNamespace(retrieval=object(), relevance_threshold=.5, context_budget=32000)
    evidence = await retrieve_knowledge('原始问题', rag=rag, filters=SearchFilters())
    assert evidence.retrieved_chunks['chunks'][0]['text'] == chunk.text
    assert evidence.retrieved_chunks['chunks'][0]['relevance_score'] == .9
