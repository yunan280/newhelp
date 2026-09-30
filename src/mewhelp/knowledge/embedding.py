"""本地 BGE-M3 dense 编码，查询与建库共用同一模型。"""

from functools import lru_cache
from threading import Lock

_load_lock = Lock()


@lru_cache
def _load_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer("BAAI/bge-m3")


def _model():
    # lru_cache 本身不保证并发首次调用只执行一次：用锁包住 cache 检查与加载。
    with _load_lock:
        return _load_model()


def embed_texts(texts: list[str]) -> list[list[float]]:
    vectors = _model().encode(texts, normalize_embeddings=True, batch_size=4)
    return vectors.tolist()
