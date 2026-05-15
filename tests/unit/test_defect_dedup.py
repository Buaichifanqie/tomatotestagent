from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from testagent.agent.defect_dedup import (
    DEFECT_HISTORY_COLLECTION,
    DUPLICATE_SIMILARITY_THRESHOLD,
    DeduplicationResult,
    DefectDeduplicator,
    _get_defect_field,
    _get_defect_id,
)
from testagent.rag.pipeline import RAGResult


@pytest.fixture
def mock_llm() -> AsyncMock:
    llm = AsyncMock()
    llm.chat = AsyncMock()
    llm.embed = AsyncMock()
    llm.embed_batch = AsyncMock()
    return llm


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
    return repo


@pytest.fixture
def deduplicator(
    mock_llm: AsyncMock,
    mock_rag: AsyncMock,
    mock_defect_repo: AsyncMock,
) -> DefectDeduplicator:
    return DefectDeduplicator(
        llm=mock_llm,
        rag=mock_rag,
        defect_repo=mock_defect_repo,
    )


@pytest.fixture
def sample_new_defect() -> dict[str, Any]:
    return {
        "id": "defect-new-001",
        "title": "Login page returns 500 error when submitting empty form",
        "description": (
            "POST /api/v1/auth/login returns 500 Internal Server Error "
            "when submitting empty credentials. Expected 400 Bad Request."
        ),
        "category": "bug",
        "severity": "critical",
        "result_id": "result-001",
    }


@pytest.fixture
def sample_existing_defect() -> dict[str, Any]:
    return {
        "id": "defect-existing-001",
        "title": "Login endpoint crashes on empty payload",
        "description": "Sending empty JSON body to /api/v1/auth/login causes unhandled exception and returns HTTP 500.",
        "category": "bug",
        "severity": "critical",
        "result_id": "result-002",
        "occurrence_count": 2,
    }


# =============================================================================
# Helper function tests
# =============================================================================


class TestGetDefectField:
    def test_dict_access(self) -> None:
        defect = {"title": "hello", "severity": "critical"}
        assert _get_defect_field(defect, "title") == "hello"
        assert _get_defect_field(defect, "severity") == "critical"
        assert _get_defect_field(defect, "nonexistent", "default") == "default"

    def test_missing_field_default(self) -> None:
        defect: dict[str, Any] = {}
        assert _get_defect_field(defect, "title") == ""


class TestGetDefectId:
    def test_dict_id(self) -> None:
        assert _get_defect_id({"id": "abc-123"}) == "abc-123"

    def test_dict_none_id(self) -> None:
        assert _get_defect_id({"id": None}) == ""

    def test_empty_defect(self) -> None:
        assert _get_defect_id({}) == ""


# =============================================================================
# DeduplicationResult tests
# =============================================================================


class TestDeduplicationResult:
    def test_to_dict_duplicate(self) -> None:
        result = DeduplicationResult(
            is_duplicate=True,
            similarity_score=0.92,
            original_defect_id="defect-existing-001",
            similar_defects=[{"doc_id": "doc-1", "similarity": 0.92}],
        )
        d = result.to_dict()
        assert d["is_duplicate"] is True
        assert d["similarity_score"] == 0.92
        assert d["original_defect_id"] == "defect-existing-001"
        assert len(d["similar_defects"]) == 1

    def test_to_dict_unique(self) -> None:
        result = DeduplicationResult(
            is_duplicate=False,
            similarity_score=0.0,
            original_defect_id=None,
            similar_defects=[],
        )
        d = result.to_dict()
        assert d["is_duplicate"] is False
        assert d["similarity_score"] == 0.0
        assert d["original_defect_id"] is None
        assert d["similar_defects"] == []


# =============================================================================
# DefectDeduplicator — check_duplicate tests
# =============================================================================


class TestCheckDuplicate:
    @pytest.mark.asyncio
    async def test_no_similar_defects_in_rag_returns_unique(
        self,
        deduplicator: DefectDeduplicator,
        mock_rag: AsyncMock,
        sample_new_defect: dict[str, Any],
    ) -> None:
        mock_rag.query.return_value = []

        result = await deduplicator.check_duplicate(sample_new_defect)

        assert result.is_duplicate is False
        assert result.similarity_score == 0.0
        assert result.original_defect_id is None
        mock_rag.query.assert_called_once()

    @pytest.mark.asyncio
    async def test_rag_query_failure_graceful_fallback(
        self,
        deduplicator: DefectDeduplicator,
        mock_rag: AsyncMock,
        sample_new_defect: dict[str, Any],
    ) -> None:
        mock_rag.query.side_effect = RuntimeError("RAG service unavailable")

        result = await deduplicator.check_duplicate(sample_new_defect)

        assert result.is_duplicate is False
        assert result.similarity_score == 0.0
        assert result.original_defect_id is None
        assert result.similar_defects == []

    @pytest.mark.asyncio
    async def test_llm_judges_duplicate_above_threshold(
        self,
        deduplicator: DefectDeduplicator,
        mock_rag: AsyncMock,
        mock_llm: AsyncMock,
        mock_defect_repo: AsyncMock,
        sample_new_defect: dict[str, Any],
    ) -> None:
        mock_rag.query.return_value = [
            RAGResult(
                doc_id="doc-001",
                content="Sending empty JSON body to /api/v1/auth/login causes HTTP 500.",
                score=0.89,
                metadata={
                    "defect_id": "defect-existing-001",
                    "defect_title": "Login endpoint crashes on empty payload",
                    "defect_category": "bug",
                    "defect_severity": "critical",
                },
            )
        ]

        mock_llm.chat.return_value = MagicMock(
            content=[
                {
                    "type": "text",
                    "text": (
                        '{"similarity_score": 0.94, "reasoning": "Both defects '
                        'describe the same issue: login endpoint returns 500 '
                        'on empty payload."}'
                    ),
                }
            ],
            stop_reason="end_turn",
            usage={},
        )

        mock_defect_repo.get_by_id.return_value = MagicMock(
            occurrence_count=2,
        )

        result = await deduplicator.check_duplicate(sample_new_defect)

        assert result.is_duplicate is True
        assert result.similarity_score >= DUPLICATE_SIMILARITY_THRESHOLD
        assert result.original_defect_id == "defect-existing-001"
        assert len(result.similar_defects) == 1
        assert result.similar_defects[0]["doc_id"] == "doc-001"

        mock_defect_repo.update.assert_called_once_with(
            "defect-existing-001",
            {"occurrence_count": 3},
        )

    @pytest.mark.asyncio
    async def test_llm_judges_below_threshold_returns_unique(
        self,
        deduplicator: DefectDeduplicator,
        mock_rag: AsyncMock,
        mock_llm: AsyncMock,
        sample_new_defect: dict[str, Any],
    ) -> None:
        mock_rag.query.return_value = [
            RAGResult(
                doc_id="doc-002",
                content="User profile page has a layout shift on mobile.",
                score=0.45,
                metadata={
                    "defect_id": "defect-existing-002",
                    "defect_title": "Profile page layout broken on mobile",
                    "defect_category": "bug",
                    "defect_severity": "minor",
                },
            )
        ]

        mock_llm.chat.return_value = MagicMock(
            content=[
                {
                    "type": "text",
                    "text": (
                        '{"similarity_score": 0.32, "reasoning": "These are '
                        'different issues: one is a login API error, the '
                        'other is a UI layout problem."}'
                    ),
                }
            ],
            stop_reason="end_turn",
            usage={},
        )

        result = await deduplicator.check_duplicate(sample_new_defect)

        assert result.is_duplicate is False
        assert result.similarity_score < DUPLICATE_SIMILARITY_THRESHOLD
        assert result.original_defect_id == "defect-existing-002"

    @pytest.mark.asyncio
    async def test_llm_response_not_json_fallback_to_zero(
        self,
        deduplicator: DefectDeduplicator,
        mock_rag: AsyncMock,
        mock_llm: AsyncMock,
        sample_new_defect: dict[str, Any],
    ) -> None:
        mock_rag.query.return_value = [
            RAGResult(
                doc_id="doc-003",
                content="Some defect content",
                score=0.7,
                metadata={
                    "defect_id": "defect-existing-003",
                    "defect_title": "Some title",
                    "defect_category": "bug",
                    "defect_severity": "major",
                },
            )
        ]

        mock_llm.chat.return_value = MagicMock(
            content=[{"type": "text", "text": "I think these are similar but I won't give JSON."}],
            stop_reason="end_turn",
            usage={},
        )

        result = await deduplicator.check_duplicate(sample_new_defect)

        assert result.is_duplicate is False
        assert result.similarity_score == 0.0

    @pytest.mark.asyncio
    async def test_llm_chat_failure_fallback_to_zero(
        self,
        deduplicator: DefectDeduplicator,
        mock_rag: AsyncMock,
        mock_llm: AsyncMock,
        sample_new_defect: dict[str, Any],
    ) -> None:
        mock_rag.query.return_value = [
            RAGResult(
                doc_id="doc-004",
                content="Some defect content",
                score=0.8,
                metadata={
                    "defect_id": "defect-existing-004",
                    "defect_title": "Some title",
                    "defect_category": "bug",
                    "defect_severity": "major",
                },
            )
        ]

        mock_llm.chat.side_effect = RuntimeError("LLM API timeout")

        result = await deduplicator.check_duplicate(sample_new_defect)

        assert result.is_duplicate is False
        assert result.similarity_score == 0.0

    @pytest.mark.asyncio
    async def test_multiple_similar_defects_takes_highest_score(
        self,
        deduplicator: DefectDeduplicator,
        mock_rag: AsyncMock,
        mock_llm: AsyncMock,
        mock_defect_repo: AsyncMock,
        sample_new_defect: dict[str, Any],
    ) -> None:
        mock_rag.query.return_value = [
            RAGResult(
                doc_id="doc-005",
                content="Login form empty submission causes 500 error.",
                score=0.85,
                metadata={
                    "defect_id": "defect-existing-005",
                    "defect_title": "Login empty form crash",
                    "defect_category": "bug",
                    "defect_severity": "critical",
                },
            ),
            RAGResult(
                doc_id="doc-006",
                content="Database connection pool exhausted under load.",
                score=0.6,
                metadata={
                    "defect_id": "defect-existing-006",
                    "defect_title": "DB pool exhaustion",
                    "defect_category": "bug",
                    "defect_severity": "major",
                },
            ),
        ]

        async def llm_chat_side_effect(
            system: str,
            messages: list[dict[str, Any]],
            tools: Any = None,
            max_tokens: int = 4096,
            temperature: float = 0.7,
        ) -> MagicMock:
            user_msg = messages[0]["content"] if messages else ""
            score = (
                0.96 if "Login empty form" in user_msg or "Login form empty" in user_msg
                else 0.12
            )
            return MagicMock(
                content=[
                    {
                        "type": "text",
                        "text": f'{{"similarity_score": {score}, "reasoning": "judgment"}}',
                    }
                ],
                stop_reason="end_turn",
                usage={},
            )

        mock_llm.chat.side_effect = llm_chat_side_effect
        mock_defect_repo.get_by_id.return_value = MagicMock(occurrence_count=1)

        result = await deduplicator.check_duplicate(sample_new_defect)

        assert result.is_duplicate is True
        assert result.similarity_score == 0.96
        assert result.original_defect_id == "defect-existing-005"
        assert len(result.similar_defects) == 2

    @pytest.mark.asyncio
    async def test_original_defect_not_found_does_not_crash(
        self,
        deduplicator: DefectDeduplicator,
        mock_rag: AsyncMock,
        mock_llm: AsyncMock,
        mock_defect_repo: AsyncMock,
        sample_new_defect: dict[str, Any],
    ) -> None:
        mock_rag.query.return_value = [
            RAGResult(
                doc_id="doc-007",
                content="Similar defect content.",
                score=0.9,
                metadata={
                    "defect_id": "defect-nonexistent",
                    "defect_title": "Similar issue",
                    "defect_category": "bug",
                    "defect_severity": "critical",
                },
            )
        ]

        mock_llm.chat.return_value = MagicMock(
            content=[
                {
                    "type": "text",
                    "text": '{"similarity_score": 0.92, "reasoning": "Same issue."}',
                }
            ],
            stop_reason="end_turn",
            usage={},
        )

        mock_defect_repo.get_by_id.return_value = None

        result = await deduplicator.check_duplicate(sample_new_defect)

        assert result.is_duplicate is True
        assert result.similarity_score >= DUPLICATE_SIMILARITY_THRESHOLD
        mock_defect_repo.update.assert_not_called()


# =============================================================================
# DefectDeduplicator — _build_search_query tests
# =============================================================================


class TestBuildSearchQuery:
    def test_full_defect_produces_query(self, deduplicator: DefectDeduplicator) -> None:
        defect = {
            "title": "Login error",
            "description": "500 error on login",
            "category": "bug",
            "severity": "critical",
        }
        query = deduplicator._build_search_query(defect)
        assert "title: Login error" in query
        assert "description: 500 error on login" in query
        assert "category: bug" in query
        assert "severity: critical" in query

    def test_minimal_defect(self, deduplicator: DefectDeduplicator) -> None:
        defect = {"title": "Only title"}
        query = deduplicator._build_search_query(defect)
        assert "title: Only title" in query
        assert "description: " in query or "description:" not in query


# =============================================================================
# DefectDeduplicator — write_back_to_rag tests
# =============================================================================


class TestWriteBackToRAG:
    @pytest.mark.asyncio
    async def test_write_back_duplicate_defect(
        self,
        deduplicator: DefectDeduplicator,
        mock_rag: AsyncMock,
        sample_new_defect: dict[str, Any],
    ) -> None:
        dedup_result = DeduplicationResult(
            is_duplicate=True,
            similarity_score=0.92,
            original_defect_id="defect-existing-001",
            similar_defects=[{"doc_id": "doc-001", "similarity": 0.92}],
        )

        await deduplicator.write_back_to_rag(sample_new_defect, dedup_result)

        mock_rag.write_back.assert_called_once()
        call_args = mock_rag.write_back.call_args[1]
        assert call_args["collection"] == DEFECT_HISTORY_COLLECTION
        assert call_args["metadata"]["defect_id"] == "defect-new-001"
        assert call_args["metadata"]["is_duplicate"] is True
        assert call_args["metadata"]["original_defect_id"] == "defect-existing-001"

    @pytest.mark.asyncio
    async def test_write_back_unique_defect(
        self,
        deduplicator: DefectDeduplicator,
        mock_rag: AsyncMock,
        sample_new_defect: dict[str, Any],
    ) -> None:
        dedup_result = DeduplicationResult(
            is_duplicate=False,
            similarity_score=0.0,
            original_defect_id=None,
            similar_defects=[],
        )

        await deduplicator.write_back_to_rag(sample_new_defect, dedup_result)

        mock_rag.write_back.assert_called_once()
        call_args = mock_rag.write_back.call_args[1]
        assert call_args["metadata"]["is_duplicate"] is False
        assert call_args["metadata"]["original_defect_id"] == ""

    @pytest.mark.asyncio
    async def test_write_back_failure_does_not_raise(
        self,
        deduplicator: DefectDeduplicator,
        mock_rag: AsyncMock,
        sample_new_defect: dict[str, Any],
    ) -> None:
        mock_rag.write_back.side_effect = RuntimeError("Write back failed")

        dedup_result = DeduplicationResult(
            is_duplicate=False,
            similarity_score=0.0,
            original_defect_id=None,
        )

        await deduplicator.write_back_to_rag(sample_new_defect, dedup_result)


# =============================================================================
# DefectDeduplicator — _increment_occurrence tests
# =============================================================================


class TestIncrementOccurrence:
    @pytest.mark.asyncio
    async def test_increments_existing_defect(
        self,
        deduplicator: DefectDeduplicator,
        mock_defect_repo: AsyncMock,
    ) -> None:
        mock_defect_repo.get_by_id.return_value = MagicMock(occurrence_count=5)

        await deduplicator._increment_occurrence("defect-existing-001")

        mock_defect_repo.update.assert_called_once_with(
            "defect-existing-001",
            {"occurrence_count": 6},
        )

    @pytest.mark.asyncio
    async def test_defect_not_found_does_not_update(
        self,
        deduplicator: DefectDeduplicator,
        mock_defect_repo: AsyncMock,
    ) -> None:
        mock_defect_repo.get_by_id.return_value = None

        await deduplicator._increment_occurrence("defect-nonexistent")

        mock_defect_repo.update.assert_not_called()

    @pytest.mark.asyncio
    async def test_repo_failure_does_not_raise(
        self,
        deduplicator: DefectDeduplicator,
        mock_defect_repo: AsyncMock,
    ) -> None:
        mock_defect_repo.get_by_id.side_effect = RuntimeError("DB connection lost")

        await deduplicator._increment_occurrence("defect-existing-001")
