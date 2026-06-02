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
    assert parse_version_gap("7.0", "8.0") == 10


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
    assert version_weight("7.45", "7.46") == pytest.approx(0.8)


def test_version_weight_cross_two():
    from testagent.memory.retrieval_post_processor import version_weight
    assert version_weight("7.45", "7.47") == pytest.approx(0.64)


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
        ),
        "case-2": SimpleNamespace(
            last_validated_version="7.42", app_version="7.42",
            confidence=0.5, execution_count=10, pass_count=9,
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
