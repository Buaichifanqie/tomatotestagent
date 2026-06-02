from __future__ import annotations

from datetime import datetime
from typing import Any

from testagent.rag.pipeline import RAGResult


def parse_version_gap(v1: str, v2: str) -> int:
    """Compute version gap as abs(minor_diff).

    Spec: only minor version matters for decay. Major version changes are
    handled by the document hard-cut path.

    Returns 0 if either version string is empty or unparseable.
    """
    if not v1 or not v2:
        return 0
    try:
        parts1 = v1.split(".")
        parts2 = v2.split(".")
        minor1 = int(parts1[1]) if len(parts1) > 1 and parts1[1] else 0
        minor2 = int(parts2[1]) if len(parts2) > 1 and parts2[1] else 0
        return abs(minor1 - minor2)
    except (ValueError, IndexError):
        return 0


# Per-type version decay bases (spec section 5.2)
VERSION_BASES = {
    "user_modified": 0.8,
    "ai_generated": 0.6,
    "manual": 0.8,
    "learned_pattern": 0.7,
    "failure_mode": 0.95,
    "specific_failure": 0.6,
}

# Per-type time decay params (spec section 5.3): (monthly_decay, floor)
TIME_DECAY_PARAMS = {
    "learned_pattern": (0.01, 0.7),
    "failure_mode": (0.01, 0.7),
    "specific_failure": (0.05, 0.3),
    "default": (0.05, 0.3),
}


def version_weight(record_version: str, current_version: str, is_document: bool = False, source: str = "ai_generated") -> float:
    """Decay weight based on version gap.

    Documents: hard-cut to 0.0 if version differs, 1.0 if same.
    Non-documents: base ** gap, where base depends on source type.
    """
    if is_document:
        return 1.0 if record_version == current_version else 0.0

    if not record_version or not current_version:
        return 1.0

    base = VERSION_BASES.get(source, 0.8)
    gap = parse_version_gap(record_version, current_version)
    return base ** gap


def update_confidence(
    initial_confidence: float,
    execution_count: int,
    pass_count: int,
    source: str = "ai_generated",
) -> float:
    """Compute dynamic confidence via weighted blend.

    Formula: blend = initial * 1/(1+0.1*n) + (pass/n) * (1 - 1/(1+0.1*n))
    where n = execution_count, initial = source-dependent initial value.

    Falls back to initial_confidence when execution_count is 0.
    """
    if execution_count <= 0:
        return _source_initial_confidence(source)

    initial = _source_initial_confidence(source)
    decay_factor = 1.0 / (1.0 + 0.1 * execution_count)
    empirical = pass_count / execution_count
    return initial * decay_factor + empirical * (1.0 - decay_factor)


def _source_initial_confidence(source: str) -> float:
    """Return spec-mandated initial confidence by source type."""
    return {
        "manual": 0.95,
        "manual_entry": 0.95,
        "user_modified": 0.85,
        "modification_delta": 0.80,
        "failure_analysis": 0.70,
        "ai_generated": 0.60,
        "generated": 0.60,
    }.get(source, 0.50)


def time_weight(created_at: datetime, now: datetime, monthly_decay: float = 0.05, floor: float = 0.3) -> float:
    """Decay weight based on age in months.

    Returns max(floor, 1.0 - monthly_decay * months).
    """
    months = (now - created_at).days / 30.0
    return max(floor, 1.0 - monthly_decay * months)


def confidence_weight(confidence: float) -> float:
    """Clamp confidence to [0.0, 1.0] and return it."""
    return max(0.0, min(1.0, confidence))


def _get_time_params(collection: str, source: str = "default") -> tuple[float, float]:
    """Return (monthly_decay, floor) for the given collection/source."""
    if collection == "app_learned_patterns":
        return TIME_DECAY_PARAMS.get("learned_pattern", TIME_DECAY_PARAMS["default"])
    return TIME_DECAY_PARAMS.get(source, TIME_DECAY_PARAMS["default"])


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
                source = getattr(record, "source", "ai_generated")
                v_w = version_weight(record_version, current_version, source=source)
                monthly, floor = _get_time_params(collection, source)
                t_w = time_weight(record.created_at, now, monthly_decay=monthly, floor=floor)
                c_w = confidence_weight(record.confidence)
            else:
                v_w = 1.0
                t_w = 1.0
                c_w = 1.0
        elif collection == "app_learned_patterns":
            record = pattern_records.get(result.doc_id)
            if record is not None:
                v_w = 1.0  # patterns don't have version decay
                source = getattr(record, "source_type", "learned_pattern")
                monthly, floor = _get_time_params(collection, source)
                t_w = time_weight(record.created_at, now, monthly_decay=monthly, floor=floor)
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
