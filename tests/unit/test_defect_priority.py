from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from testagent.agent.defect_priority import (
    API_DOCS_COLLECTION,
    DEFECT_HISTORY_COLLECTION,
    HISTORICAL_WEIGHT,
    IMPACT_WEIGHT,
    DefectPriorityEvaluator,
    PriorityResult,
    _get_defect_field,
    _get_defect_id,
    map_composite_score_to_severity,
)
from testagent.rag.pipeline import RAGResult


@pytest.fixture
def mock_rag() -> AsyncMock:
    rag = AsyncMock()
    rag.query = AsyncMock()
    rag.write_back = AsyncMock()
    return rag


@pytest.fixture
def mock_defect_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.get_by_id = AsyncMock()
    repo.update = AsyncMock()
    repo.get_by_severity = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def evaluator(
    mock_rag: AsyncMock,
    mock_defect_repo: AsyncMock,
) -> DefectPriorityEvaluator:
    return DefectPriorityEvaluator(
        defect_repo=mock_defect_repo,
        rag=mock_rag,
    )


@pytest.fixture
def sample_defect() -> dict[str, Any]:
    return {
        "id": "defect-pri-001",
        "title": "Login API returns 500 on empty credentials",
        "description": "POST /api/v1/auth/login returns 500 Internal Server Error when submitting empty credentials",
        "category": "bug",
        "severity": "critical",
        "result_id": "result-001",
    }


# =============================================================================
# Helper function tests
# =============================================================================


class TestGetDefectField:
    def test_dict_access(self) -> None:
        defect = {"title": "crash", "severity": "major"}
        assert _get_defect_field(defect, "title") == "crash"
        assert _get_defect_field(defect, "severity") == "major"

    def test_missing_field_default(self) -> None:
        defect: dict[str, Any] = {}
        assert _get_defect_field(defect, "title") == ""
        assert _get_defect_field(defect, "title", "unknown") == "unknown"


class TestGetDefectId:
    def test_dict_id(self) -> None:
        assert _get_defect_id({"id": "abc-123"}) == "abc-123"

    def test_dict_none_id(self) -> None:
        assert _get_defect_id({"id": None}) == ""

    def test_empty_defect(self) -> None:
        assert _get_defect_id({}) == ""


# =============================================================================
# Severity mapping tests
# =============================================================================


class TestMapCompositeScoreToSeverity:
    def test_critical_threshold(self) -> None:
        assert map_composite_score_to_severity(0.9) == "critical"
        assert map_composite_score_to_severity(0.75) == "critical"
        assert map_composite_score_to_severity(1.0) == "critical"

    def test_major_threshold(self) -> None:
        assert map_composite_score_to_severity(0.74) == "major"
        assert map_composite_score_to_severity(0.45) == "major"
        assert map_composite_score_to_severity(0.5) == "major"

    def test_minor_threshold(self) -> None:
        assert map_composite_score_to_severity(0.44) == "minor"
        assert map_composite_score_to_severity(0.2) == "minor"

    def test_trivial(self) -> None:
        assert map_composite_score_to_severity(0.19) == "trivial"
        assert map_composite_score_to_severity(0.0) == "trivial"
        assert map_composite_score_to_severity(0.01) == "trivial"


# =============================================================================
# PriorityResult tests
# =============================================================================


class TestPriorityResult:
    def test_to_dict(self) -> None:
        result = PriorityResult(
            defect_id="defect-001",
            suggested_severity="critical",
            impact_score=0.8567,
            historical_score=0.7234,
            affected_apis=["/api/v1/auth/login", "/api/v1/users"],
            recurrence_count=3,
        )
        d = result.to_dict()
        assert d["defect_id"] == "defect-001"
        assert d["suggested_severity"] == "critical"
        assert d["impact_score"] == 0.8567
        assert d["historical_score"] == 0.7234
        assert d["affected_apis"] == ["/api/v1/auth/login", "/api/v1/users"]
        assert d["recurrence_count"] == 3

    def test_to_dict_rounds_scores(self) -> None:
        result = PriorityResult(
            defect_id="defect-002",
            suggested_severity="minor",
            impact_score=0.123456789,
            historical_score=0.987654321,
        )
        d = result.to_dict()
        assert d["impact_score"] == 0.1235
        assert d["historical_score"] == 0.9877

    def test_default_values(self) -> None:
        result = PriorityResult(
            defect_id="defect-003",
            suggested_severity="minor",
            impact_score=0.1,
            historical_score=0.1,
        )
        assert result.affected_apis == []
        assert result.recurrence_count == 0


# =============================================================================
# DefectPriorityEvaluator — evaluate tests
# =============================================================================


class TestEvaluate:
    @pytest.mark.asyncio
    async def test_evaluate_with_affected_apis_and_historical(
        self,
        evaluator: DefectPriorityEvaluator,
        mock_rag: AsyncMock,
        sample_defect: dict[str, Any],
    ) -> None:
        api_results = [
            RAGResult(
                doc_id="api-doc-1",
                content="POST /api/v1/auth/login - Authentication endpoint",
                score=0.95,
                metadata={"api_path": "/api/v1/auth/login"},
            ),
            RAGResult(
                doc_id="api-doc-2",
                content="GET /api/v1/users - User management",
                score=0.82,
                metadata={"api_path": "/api/v1/users"},
            ),
            RAGResult(
                doc_id="api-doc-3",
                content="POST /api/v1/auth/register - Registration",
                score=0.75,
                metadata={"api_path": "/api/v1/auth/register"},
            ),
        ]

        defect_history_results = [
            RAGResult(
                doc_id="hist-1",
                content="Login endpoint crash on empty payload",
                score=0.9,
                metadata={
                    "defect_id": "defect-old-001",
                    "defect_severity": "critical",
                    "occurrence_count": 3,
                },
            ),
            RAGResult(
                doc_id="hist-2",
                content="Authentication service intermittent failures",
                score=0.7,
                metadata={
                    "defect_id": "defect-old-002",
                    "defect_severity": "major",
                    "occurrence_count": 2,
                },
            ),
        ]

        async def query_side_effect(
            query_text: str,
            collection: str,
            top_k: int = 5,
            **_kwargs: Any,
        ) -> list[RAGResult]:
            if collection == API_DOCS_COLLECTION:
                return api_results
            elif collection == DEFECT_HISTORY_COLLECTION:
                return defect_history_results
            return []

        mock_rag.query.side_effect = query_side_effect

        result = await evaluator.evaluate(sample_defect)

        assert result.defect_id == "defect-pri-001"
        assert result.suggested_severity in ("critical", "major")
        assert len(result.affected_apis) == 3
        assert "/api/v1/auth/login" in result.affected_apis
        assert result.impact_score > 0
        assert result.historical_score > 0
        assert result.recurrence_count > 0

    @pytest.mark.asyncio
    async def test_evaluate_no_rag_results(
        self,
        evaluator: DefectPriorityEvaluator,
        mock_rag: AsyncMock,
        sample_defect: dict[str, Any],
    ) -> None:
        mock_rag.query.return_value = []

        result = await evaluator.evaluate(sample_defect)

        assert result.defect_id == "defect-pri-001"
        assert result.affected_apis == []
        assert result.recurrence_count == 0
        assert result.impact_score >= 0
        assert result.historical_score == 0.0

    @pytest.mark.asyncio
    async def test_evaluate_rag_failure_graceful_fallback(
        self,
        evaluator: DefectPriorityEvaluator,
        mock_rag: AsyncMock,
        sample_defect: dict[str, Any],
    ) -> None:
        mock_rag.query.side_effect = RuntimeError("RAG service unavailable")

        result = await evaluator.evaluate(sample_defect)

        assert result.defect_id == "defect-pri-001"
        assert result.affected_apis == []
        assert result.historical_score == 0.0
        assert result.recurrence_count == 0

    @pytest.mark.asyncio
    async def test_evaluate_composite_score_formula(
        self,
        evaluator: DefectPriorityEvaluator,
        mock_rag: AsyncMock,
    ) -> None:
        defect = {
            "id": "defect-formula-001",
            "title": "Test",
            "description": "Test",
            "category": "bug",
            "severity": "major",
        }
        mock_rag.query.return_value = []

        result = await evaluator.evaluate(defect)

        expected_historical = 0.0
        expected_impact = result.impact_score
        expected_composite = expected_impact * IMPACT_WEIGHT + expected_historical * HISTORICAL_WEIGHT
        expected_severity = map_composite_score_to_severity(expected_composite)

        assert result.suggested_severity == expected_severity

    @pytest.mark.asyncio
    async def test_evaluate_minor_defect_with_low_impact(
        self,
        evaluator: DefectPriorityEvaluator,
        mock_rag: AsyncMock,
    ) -> None:
        defect = {
            "id": "defect-minor-001",
            "title": "Typo in footer",
            "description": "Spelling mistake in footer text",
            "category": "bug",
            "severity": "trivial",
        }

        mock_rag.query.return_value = []

        result = await evaluator.evaluate(defect)

        assert result.suggested_severity in ("minor", "trivial")

    @pytest.mark.asyncio
    async def test_evaluate_deduplicates_api_paths(
        self,
        evaluator: DefectPriorityEvaluator,
        mock_rag: AsyncMock,
        sample_defect: dict[str, Any],
    ) -> None:
        api_results = [
            RAGResult(
                doc_id="api-doc-1",
                content="Login endpoint",
                score=0.9,
                metadata={"api_path": "/api/v1/auth/login"},
            ),
            RAGResult(
                doc_id="api-doc-2",
                content="Login endpoint duplicate",
                score=0.85,
                metadata={"api_path": "/api/v1/auth/login"},
            ),
        ]

        async def query_side_effect(
            query_text: str,
            collection: str,
            top_k: int = 5,
            **_kwargs: Any,
        ) -> list[RAGResult]:
            if collection == API_DOCS_COLLECTION:
                return api_results
            return []

        mock_rag.query.side_effect = query_side_effect

        result = await evaluator.evaluate(sample_defect)

        assert len(result.affected_apis) == 1
        assert result.affected_apis[0] == "/api/v1/auth/login"

    @pytest.mark.asyncio
    async def test_evaluate_historical_recurrence_penalty(
        self,
        evaluator: DefectPriorityEvaluator,
        mock_rag: AsyncMock,
        sample_defect: dict[str, Any],
    ) -> None:
        defect_history_results = [
            RAGResult(
                doc_id="hist-1",
                content="Recurring login issue",
                score=0.9,
                metadata={
                    "defect_id": "defect-old-001",
                    "defect_severity": "critical",
                    "occurrence_count": 5,
                },
            ),
        ]

        async def query_side_effect(
            query_text: str,
            collection: str,
            top_k: int = 5,
            **_kwargs: Any,
        ) -> list[RAGResult]:
            if collection == API_DOCS_COLLECTION:
                return []
            elif collection == DEFECT_HISTORY_COLLECTION:
                return defect_history_results
            return []

        mock_rag.query.side_effect = query_side_effect

        result = await evaluator.evaluate(sample_defect)

        assert result.recurrence_count == 4
        assert result.historical_score > 0


# =============================================================================
# DefectPriorityEvaluator — _compute_impact_score tests
# =============================================================================


class TestComputeImpactScore:
    def test_no_affected_apis_minor_severity(
        self,
        evaluator: DefectPriorityEvaluator,
    ) -> None:
        defect = {"severity": "minor", "category": "bug"}
        score = evaluator._compute_impact_score([], defect)
        assert 0 <= score <= 1.0

    def test_many_affected_apis_critical_severity(
        self,
        evaluator: DefectPriorityEvaluator,
    ) -> None:
        defect = {"severity": "critical", "category": "bug"}
        apis = [f"/api/v1/resource-{i}" for i in range(20)]
        score = evaluator._compute_impact_score(apis, defect)
        assert score > 0.5

    def test_bug_category_boost(
        self,
        evaluator: DefectPriorityEvaluator,
    ) -> None:
        defect_bug = {"severity": "major", "category": "bug"}
        defect_env = {"severity": "major", "category": "environment"}
        apis = ["/api/v1/test"]

        score_bug = evaluator._compute_impact_score(apis, defect_bug)
        score_env = evaluator._compute_impact_score(apis, defect_env)

        assert score_bug > score_env

    def test_impact_score_capped_at_one(
        self,
        evaluator: DefectPriorityEvaluator,
    ) -> None:
        defect = {"severity": "critical", "category": "bug"}
        apis = [f"/api/v1/r-{i}" for i in range(100)]
        score = evaluator._compute_impact_score(apis, defect)
        assert score <= 1.0
