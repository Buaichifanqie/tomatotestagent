from __future__ import annotations

from datetime import datetime
from typing import Any

from testagent.rag.pipeline import RAGResult


def parse_version_gap(v1: str, v2: str) -> int:
    """Compute version gap as abs(major_diff)*10 + abs(minor_diff).

    Returns 0 if either version string is empty or unparseable.
    """
    if not v1 or not v2:
        return 0
    try:
        parts1 = v1.split(".")
        parts2 = v2.split(".")
        major1 = int(parts1[0]) if parts1[0] else 0
        minor1 = int(parts1[1]) if len(parts1) > 1 and parts1[1] else 0
        major2 = int(parts2[0]) if parts2[0] else 0
        minor2 = int(parts2[1]) if len(parts2) > 1 and parts2[1] else 0
        return abs(major1 - major2) * 10 + abs(minor1 - minor2)
    except (ValueError, IndexError):
        return 0


def version_weight(record_version: str, current_version: str, is_document: bool = False) -> float:
    """Decay weight based on version gap.

    Documents: hard-cut to 0.0 if version differs, 1.0 if same.
    Non-documents: 0.8 ** gap, or 1.0 if either version is empty.
    """
    if is_document:
        return 1.0 if record_version == current_version else 0.0

    if not record_version or not current_version:
        return 1.0

    gap = parse_version_gap(record_version, current_version)
    return 0.8 ** gap


def time_weight(created_at: datetime, now: datetime, monthly_decay: float = 0.05, floor: float = 0.3) -> float:
    """Decay weight based on age in months.

    Returns max(floor, 1.0 - monthly_decay * months).
    """
    months = (now - created_at).days / 30.0
    return max(floor, 1.0 - monthly_decay * months)


def confidence_weight(confidence: float) -> float:
    """Clamp confidence to [0.0, 1.0] and return it."""
    return max(0.0, min(1.0, confidence))


def apply_decay(
    results: list[RAGResult],
    current_version: str,
    now: datetime,
    case_records: dict[str, Any] | None = None,
    pattern_records: dict[str, Any] | None = None,
) -> list[RAGResult]:
    """Apply version, time, and confidence decay to RAG results and sort by score desc."""
    case_records = case_records or {}
    pattern_records = pattern_records or {}

    for result in results:
        collection = result.metadata.get("collection", "")
        raw = result.raw_score

        if collection == "app_documentation":
            record_version = result.metadata.get("app_version", "")
            v_w = version_weight(record_version, current_version, is_document=True)
            t_w = 1.0  # documents use hard-cut only
            c_w = 1.0
        elif collection == "app_test_cases":
            record = case_records.get(result.doc_id)
            if record is not None:
                record_version = record.last_validated_version or record.app_version
                v_w = version_weight(record_version, current_version)
                t_w = 1.0  # no time decay for cases in this phase
                c_w = confidence_weight(record.confidence)
            else:
                v_w = 1.0
                t_w = 1.0
                c_w = 1.0
        elif collection == "app_learned_patterns":
            record = pattern_records.get(result.doc_id)
            if record is not None:
                v_w = 1.0  # patterns don't have version decay
                t_w = 1.0
                c_w = confidence_weight(record.confidence)
            else:
                v_w = 1.0
                t_w = 1.0
                c_w = 1.0
        else:
            v_w = 1.0
            t_w = 1.0
            c_w = 1.0

        result.score = raw * v_w * t_w * c_w

    results.sort(key=lambda r: r.score, reverse=True)
    return results
