import sys
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from mewhelp.knowledge.store import ChunkSnapshot


def chunk(i):
    return ChunkSnapshot(
        i, "问题：蓝牙\n答案：5.3", "蓝牙", "5.3", "手册", "参数", "耳机", "manual", False, "a" * 64
    )


def test_scores_use_explicit_sigmoid_and_stable_id_ties(monkeypatch):
    from mewhelp.knowledge import reranking

    class Model:
        max_length = 100
        tokenizer = staticmethod(lambda a, b, **kwargs: {"input_ids": [0] * 10})

        def predict(self, pairs, **kwargs):
            assert kwargs["activation_fct"].__class__.__name__ == "Sigmoid"
            return [0.5, 0.9, 0.5]

    monkeypatch.setattr(reranking, "_model", lambda: Model())
    result = reranking.rerank_chunks("蓝牙", [chunk(8), chunk(7), chunk(3)])
    assert [r.chunk.id for r in result] == [7, 3, 8]
    assert [r.score for r in result] == [0.9, 0.5, 0.5]


def test_overlong_pair_is_rejected_without_truncation_before_inference(monkeypatch):
    from mewhelp.knowledge import reranking

    class Model:
        max_length = 8

        def tokenizer(self, a, b, **kwargs):
            assert kwargs["truncation"] is False
            return {"input_ids": list(range(9))}

        def predict(self, *args, **kwargs):
            raise AssertionError("must fail before inference")

    monkeypatch.setattr(reranking, "_model", lambda: Model())
    with pytest.raises(reranking.UnsupportedContextError):
        reranking.rerank_chunks("蓝牙", [chunk(1)])


def test_model_errors_propagate(monkeypatch):
    from mewhelp.knowledge import reranking

    class Model:
        max_length = 100
        tokenizer = staticmethod(lambda *a, **kw: {"input_ids": [1]})

        def predict(self, *args, **kwargs):
            raise ConnectionError("model failed")

    monkeypatch.setattr(reranking, "_model", lambda: Model())
    with pytest.raises(ConnectionError):
        reranking.rerank_chunks("蓝牙", [chunk(1)])


def test_concurrent_first_load_is_cached(monkeypatch):
    from mewhelp.knowledge import reranking

    created = []

    class CrossEncoder:
        def __init__(self, name, **kwargs):
            assert name == "BAAI/bge-reranker-v2-m3"
            created.append(self)
            time.sleep(0.03)
            self.config = SimpleNamespace(
                max_position_embeddings=514, pad_token_id=1, _commit_hash="abc123"
            )
            self.tokenizer = SimpleNamespace(model_max_length=8192)

    reranking._load_model.cache_clear()
    monkeypatch.setitem(
        sys.modules, "sentence_transformers", SimpleNamespace(CrossEncoder=CrossEncoder)
    )
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            values = list(pool.map(lambda _: reranking._model(), range(8)))
        assert len(created) == 1 and all(v is values[0] for v in values)
        assert reranking.reranker_metadata() == {
            "model_id": "BAAI/bge-reranker-v2-m3",
            "revision": "abc123",
            "max_length": 512,
            "score_transform": "sigmoid",
        }
    finally:
        reranking._load_model.cache_clear()
