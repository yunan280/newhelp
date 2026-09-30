import sys
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from mewhelp.knowledge import embedding


def test_concurrent_cold_queries_load_bge_model_once(monkeypatch):
    created = []

    class FakeModel:
        def __init__(self, name):
            assert name == "BAAI/bge-m3"
            created.append(self)
            time.sleep(0.03)

    embedding._load_model.cache_clear()
    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(SentenceTransformer=FakeModel))
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            models = list(pool.map(lambda _index: embedding._model(), range(8)))
        assert len(created) == 1
        assert all(model is created[0] for model in models)
    finally:
        embedding._load_model.cache_clear()
