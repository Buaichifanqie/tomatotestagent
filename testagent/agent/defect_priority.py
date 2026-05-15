from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from testagent.common.errors import TestAgentError
from testagent.common.logging import get_logger

if TYPE_CHECKING:
    from testagent.db.repository import DefectRepository
    from testagent.rag.pipeline import RAGPipeline

logger = get_logger(__name__)

API_DOCS_COLLECTION = "api_docs"
DEFECT_HISTORY_COLLECTION = "defect_history"

IMPACT_WEIGHT = 0.6
HISTORICAL_WEIGHT = 0.4

SEVERITY_SCORES: dict[str, float] = {
    "critical": 1.0,
    "major": 0.7,
    "minor": 0.4,
    "trivial": 0.1,
}

SEVERITY_THRESHOLDS: list[tuple[float, str]] = [
    (0.75, "critical"),
    (0.45, "major"),
    (0.2, "minor"),
]


class DefectPriorityError(TestAgentError):
    pass


@dataclass
class PriorityResult:
    defect_id: str
    suggested_severity: str
    impact_score: float
    historical_score: float
    affected_apis: list[str] = field(default_factory=list)
    recurrence_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "defect_id": self.defect_id,
            "suggested_severity": self.suggested_severity,
            "impact_score": round(self.impact_score, 4),
            "historical_score": round(self.historical_score, 4),
            "affected_apis": self.affected_apis,
            "recurrence_count": self.recurrence_count,
        }


def _get_defect_field(defect: Any, attr: str, default: Any = "") -> Any:
    if isinstance(defect, dict):
        return defect.get(attr, default)
    return getattr(defect, attr, default)


def _get_defect_id(defect: Any) -> str:
    if isinstance(defect, dict):
        raw_id: object = defect.get("id", "")
        return "" if raw_id is None else str(raw_id)
    raw_attr: object = getattr(defect, "id", "")
    return "" if raw_attr is None else str(raw_attr)


def map_composite_score_to_severity(composite: float) -> str:
    for threshold, severity in SEVERITY_THRESHOLDS:
        if composite >= threshold:
            return severity
    return "trivial"


class DefectPriorityEvaluator:
    def __init__(
        self,
        defect_repo: DefectRepository,
        rag: RAGPipeline,
    ) -> None:
        self._defect_repo = defect_repo
        self._rag = rag

    async def evaluate(self, defect: Any) -> PriorityResult:
        defect_id = _get_defect_id(defect)

        affected_apis = await self._estimate_affected_apis(defect)
        impact_score = self._compute_impact_score(affected_apis, defect)

        historical_score, recurrence_count = await self._evaluate_historical(defect)

        composite = impact_score * IMPACT_WEIGHT + historical_score * HISTORICAL_WEIGHT
        suggested_severity = map_composite_score_to_severity(composite)

        logger.info(
            "Defect priority evaluated",
            extra={
                "extra_data": {
                    "defect_id": defect_id,
                    "suggested_severity": suggested_severity,
                    "impact_score": round(impact_score, 4),
                    "historical_score": round(historical_score, 4),
                    "composite": round(composite, 4),
                    "affected_apis": affected_apis,
                    "recurrence_count": recurrence_count,
                }
            },
        )

        return PriorityResult(
            defect_id=defect_id,
            suggested_severity=suggested_severity,
            impact_score=impact_score,
            historical_score=historical_score,
            affected_apis=affected_apis,
            recurrence_count=recurrence_count,
        )

    async def _estimate_affected_apis(self, defect: Any) -> list[str]:
        title = _get_defect_field(defect, "title", "")
        description = _get_defect_field(defect, "description", "")

        query_parts: list[str] = []
        if title:
            query_parts.append(title)
        if description:
            query_parts.append(description[:500])

        query_text = " ".join(query_parts)
        if not query_text.strip():
            return []

        try:
            results = await self._rag.query(
                query_text=query_text,
                collection=API_DOCS_COLLECTION,
                top_k=10,
            )

            affected: list[str] = []
            seen_paths: set[str] = set()
            for r in results:
                api_path = r.metadata.get("api_path") or r.metadata.get("path") or ""
                if api_path and api_path not in seen_paths:
                    seen_paths.add(api_path)
                    affected.append(api_path)

            return affected
        except Exception as exc:
            logger.warning(
                "Failed to query RAG api_docs for affected APIs",
                extra={"extra_data": {"defect_id": _get_defect_id(defect), "error": str(exc)}},
            )
            return []

    def _compute_impact_score(self, affected_apis: list[str], defect: Any) -> float:
        api_count_score = min(len(affected_apis) / 10.0, 1.0)

        current_severity = _get_defect_field(defect, "severity", "minor")
        severity_base = SEVERITY_SCORES.get(current_severity, 0.4)

        category = _get_defect_field(defect, "category", "")
        category_boost = 0.1 if category == "bug" else 0.0

        raw = api_count_score * 0.5 + severity_base * 0.4 + category_boost
        return min(raw, 1.0)

    async def _evaluate_historical(self, defect: Any) -> tuple[float, int]:
        title = _get_defect_field(defect, "title", "")
        description = _get_defect_field(defect, "description", "")
        category = _get_defect_field(defect, "category", "")

        query_parts: list[str] = []
        if title:
            query_parts.append(title)
        if category:
            query_parts.append(f"category: {category}")
        if description:
            query_parts.append(description[:300])

        query_text = " | ".join(query_parts)
        if not query_text.strip():
            return 0.0, 0

        try:
            results = await self._rag.query(
                query_text=query_text,
                collection=DEFECT_HISTORY_COLLECTION,
                top_k=10,
            )
        except Exception as exc:
            logger.warning(
                "Failed to query RAG defect_history for historical evaluation",
                extra={"extra_data": {"defect_id": _get_defect_id(defect), "error": str(exc)}},
            )
            return 0.0, 0

        if not results:
            return 0.0, 0

        total_weighted_severity = 0.0
        total_weight = 0.0
        recurrence_count = 0

        for r in results:
            meta = r.metadata
            hist_severity = meta.get("defect_severity", "minor")
            severity_val = SEVERITY_SCORES.get(hist_severity, 0.4)

            weight = r.score if r.score > 0 else 0.5

            total_weighted_severity += severity_val * weight
            total_weight += weight

            occ = meta.get("occurrence_count")
            if isinstance(occ, int) and occ > 1:
                recurrence_count += occ - 1

        historical_score = total_weighted_severity / total_weight if total_weight > 0 else 0.0

        recurrence_penalty = min(recurrence_count * 0.05, 0.3)
        historical_score = min(historical_score + recurrence_penalty, 1.0)

        return historical_score, recurrence_count
