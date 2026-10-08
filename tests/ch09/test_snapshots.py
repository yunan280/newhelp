import pytest

from mewhelp.knowledge.filters import SearchFilters
from mewhelp.knowledge.retrieval import RankedChunk, RetrievalResult
from mewhelp.knowledge.store import KnowledgeChunk, snapshot_chunk


def test_snapshot_preserves_original_rank_text_score(ch09_module):
    chunks = [snapshot_chunk(KnowledgeChunk(id=9007199254740993 + i, category='中文分类',
              questions=f'原问题{i}', answer='原文不能截断' * 300, section_path='手册 / 参数',
              content_type='faq', is_key_clause=False)) for i in range(6)]
    scores = [.93, .87, .71, .55, .41, .21]
    ranked = [RankedChunk(c, score) for c, score in zip(chunks, scores, strict=True)]
    result = ch09_module('snapshots').snapshot_result(
        RetrievalResult(chunks, ranked), query='完整原问题', filters=SearchFilters(), top_k=5)
    assert result.state == 'captured'
    assert [c.chunk_id for c in result.chunks] == [str(c.id) for c in chunks[:5]]
    assert [c.rank for c in result.chunks] == [1, 2, 3, 4, 5]
    assert [c.relevance_score for c in result.chunks] == scores[:5]
    assert result.chunks[0].text == chunks[0].text
    assert result.chunks[0].answer == chunks[0].answer
    assert result.chunks[0].content_hash == chunks[0].content_hash
    assert '原文不能截断' in result.model_dump_json()


def test_legacy_null_is_not_no_retrieval(ch09_module):
    resolve = ch09_module('snapshots').resolve_evidence_snapshot
    unknown = resolve(None)
    assert unknown.state == 'unavailable'
    assert unknown.reason
    assert resolve(None, retrieval_performed=False) is None
    empty = ch09_module('snapshots').snapshot_result(
        RetrievalResult([], []), query='无召回', filters=SearchFilters())
    assert empty.state == 'empty'
    assert empty.chunks == []


def test_old_citation_recovers_partial_without_invented_score(ch09_module):
    result = ch09_module('snapshots').resolve_evidence_snapshot(None, citations=[{
        'chunk_id': '99', 'questions': '防水吗？', 'answer': '不防水。',
        'section_path': '手册', 'content_hash': 'hash', 'category': '说明书'}])
    assert result.state == 'legacy_partial'
    assert result.chunks[0].relevance_score is None
    assert result.chunks[0].answer == '不防水。'


@pytest.mark.parametrize('score', [float('nan'), float('inf'), -1, 1.01])
def test_corrupt_score_cannot_become_captured_evidence(ch09_module, score):
    c = snapshot_chunk(KnowledgeChunk(id=1, category='FAQ', questions='问', answer='答',
                                     content_type='faq', is_key_clause=False))
    with pytest.raises(ValueError):
        ch09_module('snapshots').snapshot_result(RetrievalResult([c], [RankedChunk(c, score)]),
                                                query='问', filters=SearchFilters())
