"""Metrics with explicit undefined values; duplicate results count once."""


def _top(ranked: list[int], k: int) -> list[int]:
    if type(k) is not int or k <= 0:
        raise ValueError("k must be a positive integer")
    return list(dict.fromkeys(ranked))[:k]


def recall_at_k(ranked: list[int], relevant: set[int], k: int) -> float | None:
    top = _top(ranked, k)
    return len(set(top) & relevant) / len(relevant) if relevant else None


def reciprocal_rank_at_k(ranked: list[int], relevant: set[int], k: int) -> float | None:
    top = _top(ranked, k)
    if not relevant:
        return None
    return next((1 / rank for rank, chunk_id in enumerate(top, 1) if chunk_id in relevant), 0.0)


def faithfulness(supported: list[bool]) -> float | None:
    if any(type(item) is not bool for item in supported):
        raise ValueError("claim support labels must be boolean")
    return sum(supported) / len(supported) if supported else None
