from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from testagent.rag.pipeline import RAGResult


# ---------------------------------------------------------------------------
# parse_version_gap
# ---------------------------------------------------------------------------

def test_parse_version_gap_same():
    from testagent.memory.retrieval_post_processor import parse_version_gap
    assert parse_version_gap("7.45", "7.45") == 0


def test_parse_version_gap_one_minor():
    from testagent.memory.retrieval_post_processor import parse_version_gap
    assert parse_version_gap("7.45", "7.46") == 1


def test_parse_version_gap_multiple_minor():
    from testagent.memory.retrieval_post_processor import parse_version_gap
    assert parse_version_gap("7.45", "7.48") == 3


def test_parse_version_gap_major():
    from testagent.memory.retrieval_post_processor import parse_version_gap
    # Spec: only minor version matters; major gap yields 0 (handled by document hard-cut)
    assert parse_version_gap("7.0", "8.0") == 0


def test_parse_version_gap_empty():
    from testagent.memory.retrieval_post_processor import parse_version_gap
    assert parse_version_gap("", "7.45") == 0
    assert parse_version_gap("7.45", "") == 0
    assert parse_version_gap("", "") == 0


# ---------------------------------------------------------------------------
# version_weight
# ---------------------------------------------------------------------------

def test_version_weight_same_version():
    from testagent.memory.retrieval_post_processor import version_weight
    assert version_weight("7.45", "7.45") == 1.0


def test_version_weight_cross_one():
    from testagent.memory.retrieval_post_processor import version_weight
    # Default source=ai_generated, base=0.6
    assert version_weight("7.45", "7.46") == pytest.approx(0.6)


def test_version_weight_cross_two():
    from testagent.memory.retrieval_post_processor import version_weight
    # Default source=ai_generated, base=0.6, gap=2 → 0.36
    assert version_weight("7.45", "7.47") == pytest.approx(0.36)


def test_version_weight_user_modified_cross_one():
    from testagent.memory.retrieval_post_processor import version_weight
    assert version_weight("7.45", "7.46", source="user_modified") == pytest.approx(0.8)


def test_version_weight_ai_generated_base():
    from testagent.memory.retrieval_post_processor import version_weight
    # ai_generated base=0.6, gap=1 → 0.6
    assert version_weight("7.45", "7.46", source="ai_generated") == pytest.approx(0.6)


def test_version_weight_learned_pattern_base():
    from testagent.memory.retrieval_post_processor import version_weight
    # learned_pattern base=0.7, gap=1 → 0.7
    assert version_weight("7.45", "7.46", source="learned_pattern") == pytest.approx(0.7)


def test_version_weight_document_hard_cut():
    from testagent.memory.retrieval_post_processor import version_weight
    assert version_weight("7.45", "7.46", is_document=True) == 0.0


def test_version_weight_document_same():
    from testagent.memory.retrieval_post_processor import version_weight
    assert version_weight("7.45", "7.45", is_document=True) == 1.0


def test_version_weight_empty_version():
    from testagent.memory.retrieval_post_processor import version_weight
    assert version_weight("", "7.45") == 1.0
    assert version_weight("7.45", "") == 1.0


# ---------------------------------------------------------------------------
# time_weight
# ---------------------------------------------------------------------------

def test_time_weight_recent():
    from testagent.memory.retrieval_post_processor import time_weight
    now = datetime(2026, 6, 1)
    created = datetime(2026, 5, 31)
    w = time_weight(created, now)
    assert w == pytest.approx(1.0, abs=0.02)


def test_time_weight_six_months():
    from testagent.memory.retrieval_post_processor import time_weight
    now = datetime(2026, 6, 1)
    created = datetime(2025, 12, 1)
    w = time_weight(created, now)
    assert w == pytest.approx(0.70, abs=0.05)


def test_time_weight_very_old():
    from testagent.memory.retrieval_post_processor import time_weight
    now = datetime(2026, 6, 1)
    created = datetime(2020, 1, 1)
    w = time_weight(created, now)
    assert w == 0.30


# ---------------------------------------------------------------------------
# confidence_weight
# ---------------------------------------------------------------------------

def test_confidence_weight_normal():
    from testagent.memory.retrieval_post_processor import confidence_weight
    assert confidence_weight(0.7) == 0.7
    assert confidence_weight(0.0) == 0.0
    assert confidence_weight(1.0) == 1.0


def test_confidence_weight_clamped():
    from testagent.memory.retrieval_post_processor import confidence_weight
    assert confidence_weight(1.5) == 1.0
    assert confidence_weight(-0.1) == 0.0


# ---------------------------------------------------------------------------
# update_confidence
# ---------------------------------------------------------------------------


def test_update_confidence_zero_executions():
    from testagent.memory.retrieval_post_processor import update_confidence
    # No executions → returns source-dependent initial value
    assert update_confidence(0.5, 0, 0, source="ai_generated") == pytest.approx(0.60)


def test_update_confidence_with_executions():
    from testagent.memory.retrieval_post_processor import update_confidence
    # 10 executions, 8 passes, ai_generated initial=0.60
    # decay_factor = 1/(1+0.1*10) = 0.5
    # blend = 0.60*0.5 + 0.8*0.5 = 0.70
    result = update_confidence(0.60, 10, 8, source="ai_generated")
    assert result == pytest.approx(0.70)


def test_update_confidence_manual_source():
    from testagent.memory.retrieval_post_processor import update_confidence
    # manual initial=0.95, 5 executions, 4 passes
    # decay = 1/(1+0.5) ≈ 0.667
    # blend = 0.95*0.667 + 0.8*0.333 ≈ 0.90
    result = update_confidence(0.95, 5, 4, source="manual")
    assert result > 0.85
    assert result < 0.95


# ---------------------------------------------------------------------------
# time_weight with per-type params
# ---------------------------------------------------------------------------


def test_time_weight_learned_pattern_slower_decay():
    from testagent.memory.retrieval_post_processor import time_weight
    now = datetime(2026, 6, 1)
    created = datetime(2025, 12, 1)  # 6 months ago
    # learned_pattern: monthly_decay=0.01, floor=0.7
    w = time_weight(created, now, monthly_decay=0.01, floor=0.7)
    assert w == pytest.approx(0.94, abs=0.02)


def test_time_weight_learned_pattern_floor():
    from testagent.memory.retrieval_post_processor import time_weight
    now = datetime(2026, 6, 1)
    created = datetime(2020, 1, 1)  # very old
    w = time_weight(created, now, monthly_decay=0.01, floor=0.7)
    assert w == 0.7


# ---------------------------------------------------------------------------
# apply_decay
# ---------------------------------------------------------------------------

def test_apply_decay_reorders_results():
    """Cross-version result should score lower than same-version result."""
    from testagent.memory.retrieval_post_processor import apply_decay

    now = datetime(2026, 6, 1)

    # Same-version case (high score expected)
    r1 = RAGResult(
        doc_id="case-1",
        content="same version",
        score=1.0,
        raw_score=1.0,
        metadata={"collection": "app_test_cases"},
    )
    # Cross-version case (lower score expected due to version decay)
    r2 = RAGResult(
        doc_id="case-2",
        content="old version",
        score=1.0,
        raw_score=1.0,
        metadata={"collection": "app_test_cases"},
    )

    case_records = {
        "case-1": SimpleNamespace(
            last_validated_version="7.45", app_version="7.45",
            confidence=0.5, execution_count=10, pass_count=9,
            source="ai_generated", created_at=datetime(2026, 5, 1),
        ),
        "case-2": SimpleNamespace(
            last_validated_version="7.42", app_version="7.42",
            confidence=0.5, execution_count=10, pass_count=9,
            source="ai_generated", created_at=datetime(2026, 5, 1),
        ),
    }

    current_version = "7.45"
    # Pass r2 first, but after decay r1 should come first
    results = apply_decay([r2, r1], current_version, now, case_records=case_records)

    assert results[0].doc_id == "case-1"
    assert results[1].doc_id == "case-2"
    assert results[0].score > results[1].score


def test_apply_decay_document_hard_cut_zeroed():
    """Document with old version gets score 0."""
    from testagent.memory.retrieval_post_processor import apply_decay

    now = datetime(2026, 6, 1)

    r = RAGResult(
        doc_id="doc-old",
        content="old doc",
        score=1.0,
        raw_score=1.0,
        metadata={"collection": "app_documentation", "app_version": "7.44"},
    )

    results = apply_decay([r], "7.45", now)

    assert results[0].score == 0.0
