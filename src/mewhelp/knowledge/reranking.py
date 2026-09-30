"""Fixed local BGE reranker with explicit scoring and no silent truncation."""

import math
from functools import lru_cache
from threading import Lock

from .retrieval import RankedChunk
from .store import ChunkSnapshot

MODEL_ID = "BAAI/bge-reranker-v2-m3"
_load_lock = Lock()
_inference_lock = Lock()


class UnsupportedContextError(ValueError):
    """A complete evidence pair does not fit the verified model budget."""


@lru_cache
def _load_model():
    import torch
    from sentence_transformers import CrossEncoder

    model = CrossEncoder(MODEL_ID, default_activation_function=torch.nn.Sigmoid())
    # XLM-R positions start after padding_idx; special tokens are included by tokenizer.
    position_limit = int(model.config.max_position_embeddings) - int(model.config.pad_token_id) - 1
    tokenizer_limit = int(model.tokenizer.model_max_length)
    model.max_length = min(position_limit, tokenizer_limit)
    if model.max_length < 1:
        raise RuntimeError("invalid reranker config/tokenizer context limit")
    return model


def _model():
    with _load_lock:
        return _load_model()


def reranker_metadata() -> dict:
    model = _model()
    revision = getattr(model.config, "_commit_hash", None)
    if not revision:
        raise RuntimeError("reranker revision could not be verified")
    return {
        "model_id": MODEL_ID,
        "revision": revision,
        "max_length": model.max_length,
        "score_transform": "sigmoid",
    }


def rerank_chunks(question: str, chunks: list[ChunkSnapshot]) -> list[RankedChunk]:
    if not chunks:
        return []
    import torch

    model = _model()
    pairs = [(question, item.text) for item in chunks]
    with _inference_lock:
        for question_text, body in pairs:
            encoded = model.tokenizer(
                question_text, body, truncation=False, add_special_tokens=True
            )
            if len(encoded["input_ids"]) > model.max_length:
                raise UnsupportedContextError(f"reranker pair exceeds {model.max_length} tokens")
        scores = model.predict(
            pairs, batch_size=4, show_progress_bar=False, activation_fct=torch.nn.Sigmoid()
        )
    if len(scores) != len(chunks) or any(not math.isfinite(float(score)) for score in scores):
        raise RuntimeError("reranker returned invalid scores")
    ranked = [RankedChunk(item, float(score)) for item, score in zip(chunks, scores, strict=True)]
    return sorted(ranked, key=lambda item: (-item.score, item.chunk.id))
