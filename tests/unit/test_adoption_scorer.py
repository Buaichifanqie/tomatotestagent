"""Tests for adoption scorer — TDD red phase."""
import pytest

from testagent.memory.adoption_scorer import _cosine_similarity, compute_adoption_score


# ---------------------------------------------------------------------------
# _cosine_similarity
# ---------------------------------------------------------------------------


def test_cosine_similarity_identical():
    """Identical vectors → similarity 1.0."""
    v = [1.0, 2.0, 3.0]
    assert _cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal():
    """Orthogonal vectors → similarity 0.0."""
    a = [1.0, 0.0, 0.0]
    b = [0.0, 1.0, 0.0]
    assert _cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_opposite():
    """Opposite vectors → similarity -1.0."""
    a = [1.0, 0.0, 0.0]
    b = [-1.0, 0.0, 0.0]
    assert _cosine_similarity(a, b) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector():
    """Zero vector → 0.0 (avoid division by zero)."""
    a = [0.0, 0.0, 0.0]
    b = [1.0, 2.0, 3.0]
    assert _cosine_similarity(a, b) == 0.0


# ---------------------------------------------------------------------------
# compute_adoption_score — edge cases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adoption_empty_retrieved():
    """No retrieved items → score 0.0."""

    async def embed(text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    score = await compute_adoption_score(
        generated_cases=["some case"],
        retrieved_items=[],
        embed_fn=embed,
    )
    assert score == 0.0


@pytest.mark.asyncio
async def test_adoption_empty_generated():
    """No generated cases → score 0.0."""

    async def embed(text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    score = await compute_adoption_score(
        generated_cases=[],
        retrieved_items=[{"content_preview": "something"}],
        embed_fn=embed,
    )
    assert score == 0.0


# ---------------------------------------------------------------------------
# compute_adoption_score — full / none / partial
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_adoption_all_adopted():
    """Embed returns same vector for every input → all items above threshold."""

    async def same_embed(text: str) -> list[float]:
        return [1.0, 0.0, 0.0]

    items = [
        {"content_preview": "item A"},
        {"content_preview": "item B"},
        {"content_preview": "item C"},
    ]
    score = await compute_adoption_score(
        generated_cases=["case one"],
        retrieved_items=items,
        embed_fn=same_embed,
        threshold=0.85,
    )
    assert score == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_adoption_none_adopted():
    """Embed returns orthogonal vectors for generated vs. retrieved → score 0.0."""

    async def orthogonal_embed(text: str) -> list[float]:
        if "generated" in text.lower() or "case" in text.lower():
            return [1.0, 0.0, 0.0]
        return [0.0, 1.0, 0.0]

    items = [
        {"content_preview": "retrieved item A"},
        {"content_preview": "retrieved item B"},
    ]
    score = await compute_adoption_score(
        generated_cases=["case generated content"],
        retrieved_items=items,
        embed_fn=orthogonal_embed,
        threshold=0.85,
    )
    assert score == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_adoption_partial():
    """Mix of adopted and non-adopted items → score between 0 and 1."""
    call_count = 0

    async def alternating_embed(text: str) -> list[float]:
        nonlocal call_count
        call_count += 1
        # First call is for generated_cases; subsequent calls are per-item.
        # Return same vector for items 1 & 3, orthogonal for item 2.
        if call_count == 1:
            return [1.0, 0.0, 0.0]
        if call_count % 2 == 0:
            # even calls (item index 0, 2) → same direction
            return [1.0, 0.0, 0.0]
        # odd calls (item index 1) → orthogonal
        return [0.0, 1.0, 0.0]

    items = [
        {"content_preview": "item A"},  # call 2 → same → adopted
        {"content_preview": "item B"},  # call 3 → orthogonal → not adopted
        {"content_preview": "item C"},  # call 4 → same → adopted
    ]
    score = await compute_adoption_score(
        generated_cases=["case one"],
        retrieved_items=items,
        embed_fn=alternating_embed,
        threshold=0.85,
    )
    assert 0.0 < score < 1.0
    # 2 out of 3 adopted
    assert score == pytest.approx(2.0 / 3.0)


@pytest.mark.asyncio
async def test_adoption_threshold_boundary():
    """Similarity exactly at threshold is NOT counted (uses > not >=)."""
    # We need two vectors whose cosine similarity == threshold exactly.
    # For threshold=0.85, pick vectors with dot/(norm_a*norm_b) == 0.85.
    # a = [1, 0],  b = [0.85, sqrt(1-0.85^2)] → sim == 0.85 exactly
    import math

    threshold = 0.85
    b_y = math.sqrt(1.0 - threshold ** 2)

    async def embed_a(text: str) -> list[float]:
        return [1.0, 0.0]

    async def embed_b(text: str) -> list[float]:
        return [threshold, b_y]

    # Verify our vectors actually give the threshold
    assert _cosine_similarity([1.0, 0.0], [threshold, b_y]) == pytest.approx(threshold)

    # generated embeds as [1,0], item embeds as [0.85, b_y] → sim == 0.85
    # Since condition is `sim > threshold`, this should NOT be adopted.
    items = [{"content_preview": "edge case item"}]
    score = await compute_adoption_score(
        generated_cases=["generated case"],
        retrieved_items=items,
        embed_fn=lambda text: embed_a(text) if "generated" in text else embed_b(text),
        threshold=threshold,
    )
    assert score == pytest.approx(0.0)
