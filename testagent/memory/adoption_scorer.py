"""Adoption scorer — measures how many retrieved items overlap with generated cases."""
from __future__ import annotations

import math
from typing import Awaitable, Callable


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Standard cosine similarity between two vectors.

    Returns 0.0 if either vector has zero norm.
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


async def compute_adoption_score(
    generated_cases: list[str],
    retrieved_items: list[dict],
    embed_fn: Callable[[str], Awaitable[list[float]]],
    threshold: float = 0.85,
) -> float:
    """Compute the adoption score for generated test cases vs retrieved items.

    Parameters
    ----------
    generated_cases:
        List of generated case text strings.
    retrieved_items:
        List of dicts with at least ``{"content_preview": str}`` key.
    embed_fn:
        Async callable that takes a string and returns ``list[float]``.
    threshold:
        Cosine similarity threshold for "adopted" (default 0.85).

    Returns
    -------
    float
        Fraction of retrieved items that are "adopted" (0.0 - 1.0).
    """
    if not retrieved_items:
        return 0.0
    if not generated_cases:
        return 0.0

    generated_text = " ".join(generated_cases)
    generated_emb = await embed_fn(generated_text)

    adopted = 0
    for item in retrieved_items:
        content = item.get("content_preview", "")
        if not content:
            continue
        item_emb = await embed_fn(content)
        sim = _cosine_similarity(generated_emb, item_emb)
        if sim > threshold:
            adopted += 1

    return adopted / len(retrieved_items)
