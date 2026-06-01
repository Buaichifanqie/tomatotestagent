from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from testagent.cli.main import app

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@dataclass
class FakePattern:
    id: str = "pat-001"
    app_id: str = "com.example.app"
    pattern: str = "always tap confirm twice"
    pattern_type: str = "behavior"
    source_type: str = "manual_entry"
    confidence: float = 0.8
    review_status: str = "pending"
    review_reason: str | None = None
    occurrence_count: int = 3
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class FakeTrace:
    id: str = "trace-001"
    app_id: str = "com.example.app"
    query: str = "test search"
    query_stage: str = "stage1"
    retrieved_items: list[dict[str, Any]] | None = None
    generated_case_ids: list[str] | None = None
    adoption_score: float | None = 0.75
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class FakeRAGResult:
    doc_id: str = "doc-001"
    content: str = "some content"
    score: float = 0.9
    metadata: dict[str, Any] = field(default_factory=dict)


def _make_mock_session_ctx(mock_session: AsyncMock) -> MagicMock:
    """Create a context manager mock that yields mock_session."""
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


# ---------------------------------------------------------------------------
# list-patterns
# ---------------------------------------------------------------------------

class TestMemoryListPatterns:
    def test_help(self) -> None:
        result = runner.invoke(app, ["memory", "list-patterns", "--help"])
        assert result.exit_code == 0
        assert "app_id" in result.stdout

    def test_list_patterns_default(self) -> None:
        fake_patterns = [
            FakePattern(id="p1", pattern="pattern A", pattern_type="behavior", confidence=0.9, review_status="approved", occurrence_count=5),
            FakePattern(id="p2", pattern="pattern B", pattern_type="workaround", confidence=0.3, review_status="pending", occurrence_count=1),
        ]

        async def _fake_get_by_app_id(app_id: str, status_filter: str | None = None, limit: int = 20):
            return fake_patterns

        mock_repo = MagicMock()
        mock_repo.get_by_app_id = AsyncMock(side_effect=_fake_get_by_app_id)

        with (
            patch("testagent.db.engine.get_session", return_value=_make_mock_session_ctx(AsyncMock())),
            patch("testagent.db.repository.LearnedPatternRepository", return_value=mock_repo),
        ):
            result = runner.invoke(app, ["memory", "list-patterns", "com.example.app"])
            assert result.exit_code == 0
            assert "p1" in result.stdout
            assert "p2" in result.stdout

    def test_list_patterns_with_status_filter(self) -> None:
        async def _fake_get_by_app_id(app_id: str, status_filter: str | None = None, limit: int = 20):
            assert status_filter == "approved"
            return [FakePattern(id="p1", review_status="approved")]

        mock_repo = MagicMock()
        mock_repo.get_by_app_id = AsyncMock(side_effect=_fake_get_by_app_id)

        with (
            patch("testagent.db.engine.get_session", return_value=_make_mock_session_ctx(AsyncMock())),
            patch("testagent.db.repository.LearnedPatternRepository", return_value=mock_repo),
        ):
            result = runner.invoke(app, ["memory", "list-patterns", "com.example.app", "--status", "approved"])
            assert result.exit_code == 0
            assert "p1" in result.stdout

    def test_list_patterns_empty(self) -> None:
        mock_repo = MagicMock()
        mock_repo.get_by_app_id = AsyncMock(return_value=[])

        with (
            patch("testagent.db.engine.get_session", return_value=_make_mock_session_ctx(AsyncMock())),
            patch("testagent.db.repository.LearnedPatternRepository", return_value=mock_repo),
        ):
            result = runner.invoke(app, ["memory", "list-patterns", "com.example.app"])
            assert result.exit_code == 0
            assert "No patterns found" in result.stdout


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------

class TestMemoryApprove:
    def test_help(self) -> None:
        result = runner.invoke(app, ["memory", "approve", "--help"])
        assert result.exit_code == 0
        assert "pattern_id" in result.stdout

    def test_approve_success(self) -> None:
        mock_repo = MagicMock()
        mock_repo.approve = AsyncMock(return_value=FakePattern(id="pat-001", review_status="approved"))

        with (
            patch("testagent.db.engine.get_session", return_value=_make_mock_session_ctx(AsyncMock())),
            patch("testagent.db.repository.LearnedPatternRepository", return_value=mock_repo),
        ):
            result = runner.invoke(app, ["memory", "approve", "pat-001"])
            assert result.exit_code == 0
            assert "approved" in result.stdout.lower()

    def test_approve_not_found(self) -> None:
        mock_repo = MagicMock()
        mock_repo.approve = AsyncMock(return_value=None)

        with (
            patch("testagent.db.engine.get_session", return_value=_make_mock_session_ctx(AsyncMock())),
            patch("testagent.db.repository.LearnedPatternRepository", return_value=mock_repo),
        ):
            result = runner.invoke(app, ["memory", "approve", "nonexistent"])
            assert result.exit_code == 1
            assert "not found" in result.stdout.lower()


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------

class TestMemoryReject:
    def test_help(self) -> None:
        result = runner.invoke(app, ["memory", "reject", "--help"])
        assert result.exit_code == 0
        assert "pattern_id" in result.stdout

    def test_reject_success(self) -> None:
        mock_repo = MagicMock()
        mock_repo.reject = AsyncMock(return_value=FakePattern(id="pat-001", review_status="rejected", review_reason="bad data"))

        with (
            patch("testagent.db.engine.get_session", return_value=_make_mock_session_ctx(AsyncMock())),
            patch("testagent.db.repository.LearnedPatternRepository", return_value=mock_repo),
        ):
            result = runner.invoke(app, ["memory", "reject", "pat-001", "--reason", "bad data"])
            assert result.exit_code == 0
            assert "rejected" in result.stdout.lower()

    def test_reject_not_found(self) -> None:
        mock_repo = MagicMock()
        mock_repo.reject = AsyncMock(return_value=None)

        with (
            patch("testagent.db.engine.get_session", return_value=_make_mock_session_ctx(AsyncMock())),
            patch("testagent.db.repository.LearnedPatternRepository", return_value=mock_repo),
        ):
            result = runner.invoke(app, ["memory", "reject", "nonexistent"])
            assert result.exit_code == 1
            assert "not found" in result.stdout.lower()


# ---------------------------------------------------------------------------
# add-pattern
# ---------------------------------------------------------------------------

class TestMemoryAddPattern:
    def test_help(self) -> None:
        result = runner.invoke(app, ["memory", "add-pattern", "--help"])
        assert result.exit_code == 0
        assert "app_id" in result.stdout
        assert "pattern_text" in result.stdout

    def test_add_pattern_default_type(self) -> None:
        mock_repo = MagicMock()
        mock_repo.create = AsyncMock(return_value=FakePattern(id="new-pat"))

        mock_pipeline = MagicMock()
        mock_pipeline.write_back = AsyncMock()

        with (
            patch("testagent.db.engine.get_session", return_value=_make_mock_session_ctx(AsyncMock())),
            patch("testagent.db.repository.LearnedPatternRepository", return_value=mock_repo),
            patch("testagent.config.settings.get_settings", return_value=MagicMock()),
            patch("testagent.rag.factories.create_pipeline", return_value=mock_pipeline),
        ):
            result = runner.invoke(app, ["memory", "add-pattern", "com.example.app", "tap confirm twice"])
            assert result.exit_code == 0
            assert "new-pat" in result.stdout
            mock_repo.create.assert_called_once()
            mock_pipeline.write_back.assert_called_once()

    def test_add_pattern_custom_type(self) -> None:
        mock_repo = MagicMock()
        created_pattern = FakePattern(id="new-pat", pattern_type="workaround")
        mock_repo.create = AsyncMock(return_value=created_pattern)

        mock_pipeline = MagicMock()
        mock_pipeline.write_back = AsyncMock()

        with (
            patch("testagent.db.engine.get_session", return_value=_make_mock_session_ctx(AsyncMock())),
            patch("testagent.db.repository.LearnedPatternRepository", return_value=mock_repo),
            patch("testagent.config.settings.get_settings", return_value=MagicMock()),
            patch("testagent.rag.factories.create_pipeline", return_value=mock_pipeline),
        ):
            result = runner.invoke(app, [
                "memory", "add-pattern", "com.example.app", "swipe to dismiss",
                "--type", "workaround",
            ])
            assert result.exit_code == 0
            # Verify the pattern was created with correct type
            call_args = mock_repo.create.call_args
            pattern_arg = call_args[0][0]
            assert pattern_arg.pattern_type == "workaround"

    def test_add_pattern_writes_to_rag(self) -> None:
        mock_repo = MagicMock()
        mock_repo.create = AsyncMock(return_value=FakePattern(id="new-pat"))

        mock_pipeline = MagicMock()
        mock_pipeline.write_back = AsyncMock()

        with (
            patch("testagent.db.engine.get_session", return_value=_make_mock_session_ctx(AsyncMock())),
            patch("testagent.db.repository.LearnedPatternRepository", return_value=mock_repo),
            patch("testagent.config.settings.get_settings", return_value=MagicMock()),
            patch("testagent.rag.factories.create_pipeline", return_value=mock_pipeline),
        ):
            result = runner.invoke(app, ["memory", "add-pattern", "com.example.app", "some pattern"])
            assert result.exit_code == 0
            mock_pipeline.write_back.assert_called_once()
            call_kwargs = mock_pipeline.write_back.call_args
            assert call_kwargs[1]["collection"] == "app_learned_patterns"
            assert call_kwargs[1]["chunk_size"] == 256


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------

class TestMemorySearch:
    def test_help(self) -> None:
        result = runner.invoke(app, ["memory", "search", "--help"])
        assert result.exit_code == 0
        assert "app_id" in result.stdout
        assert "query" in result.stdout

    def test_search_case_type(self) -> None:
        fake_results = [FakeRAGResult(doc_id="tc-1", content="test case content", score=0.95)]
        mock_pipeline = MagicMock()
        mock_pipeline.query = AsyncMock(return_value=fake_results)

        with (
            patch("testagent.config.settings.get_settings", return_value=MagicMock()),
            patch("testagent.rag.factories.create_pipeline", return_value=mock_pipeline),
        ):
            result = runner.invoke(app, [
                "memory", "search", "com.example.app", "login test",
                "--type", "case",
            ])
            assert result.exit_code == 0
            assert "tc-1" in result.stdout
            mock_pipeline.query.assert_called_once()
            call_kwargs = mock_pipeline.query.call_args
            assert call_kwargs[1]["collection"] == "app_test_cases"

    def test_search_pattern_type(self) -> None:
        fake_results = [FakeRAGResult(doc_id="pat-1", content="pattern content", score=0.88)]
        mock_pipeline = MagicMock()
        mock_pipeline.query = AsyncMock(return_value=fake_results)

        with (
            patch("testagent.config.settings.get_settings", return_value=MagicMock()),
            patch("testagent.rag.factories.create_pipeline", return_value=mock_pipeline),
        ):
            result = runner.invoke(app, [
                "memory", "search", "com.example.app", "swipe gesture",
                "--type", "pattern",
            ])
            assert result.exit_code == 0
            assert "pat-1" in result.stdout
            call_kwargs = mock_pipeline.query.call_args
            assert call_kwargs[1]["collection"] == "app_learned_patterns"

    def test_search_no_type_queries_both(self) -> None:
        fake_cases = [FakeRAGResult(doc_id="tc-1", content="case content", score=0.9)]
        fake_patterns = [FakeRAGResult(doc_id="pat-1", content="pattern content", score=0.8)]

        call_count = 0

        async def _fake_query(query_text: str, collection: str, top_k: int = 5, filters: dict[str, Any] | None = None):
            nonlocal call_count
            call_count += 1
            if collection == "app_test_cases":
                return fake_cases
            return fake_patterns

        mock_pipeline = MagicMock()
        mock_pipeline.query = AsyncMock(side_effect=_fake_query)

        with (
            patch("testagent.config.settings.get_settings", return_value=MagicMock()),
            patch("testagent.rag.factories.create_pipeline", return_value=mock_pipeline),
        ):
            result = runner.invoke(app, [
                "memory", "search", "com.example.app", "login",
            ])
            assert result.exit_code == 0
            assert call_count == 2

    def test_search_no_results(self) -> None:
        mock_pipeline = MagicMock()
        mock_pipeline.query = AsyncMock(return_value=[])

        with (
            patch("testagent.config.settings.get_settings", return_value=MagicMock()),
            patch("testagent.rag.factories.create_pipeline", return_value=mock_pipeline),
        ):
            result = runner.invoke(app, [
                "memory", "search", "com.example.app", "nonexistent",
                "--type", "case",
            ])
            assert result.exit_code == 0
            assert "No results found" in result.stdout

    def test_search_custom_top_k(self) -> None:
        mock_pipeline = MagicMock()
        mock_pipeline.query = AsyncMock(return_value=[])

        with (
            patch("testagent.config.settings.get_settings", return_value=MagicMock()),
            patch("testagent.rag.factories.create_pipeline", return_value=mock_pipeline),
        ):
            result = runner.invoke(app, [
                "memory", "search", "com.example.app", "test",
                "--type", "case",
                "--top-k", "10",
            ])
            assert result.exit_code == 0
            call_kwargs = mock_pipeline.query.call_args
            assert call_kwargs[1]["top_k"] == 10


# ---------------------------------------------------------------------------
# trace
# ---------------------------------------------------------------------------

class TestMemoryTrace:
    def test_help(self) -> None:
        result = runner.invoke(app, ["memory", "trace", "--help"])
        assert result.exit_code == 0
        assert "app_id" in result.stdout

    def test_trace_shows_traces(self) -> None:
        fake_traces = [
            FakeTrace(id="t1", query="login test", query_stage="stage1", adoption_score=0.8),
            FakeTrace(id="t2", query="search test", query_stage="stage2", adoption_score=0.5),
        ]

        mock_repo = MagicMock()
        mock_repo.get_by_app_id = AsyncMock(return_value=fake_traces)

        with (
            patch("testagent.db.engine.get_session", return_value=_make_mock_session_ctx(AsyncMock())),
            patch("testagent.db.repository.RetrievalTraceRepository", return_value=mock_repo),
        ):
            result = runner.invoke(app, ["memory", "trace", "com.example.app"])
            assert result.exit_code == 0
            assert "t1" in result.stdout
            assert "t2" in result.stdout

    def test_trace_empty(self) -> None:
        mock_repo = MagicMock()
        mock_repo.get_by_app_id = AsyncMock(return_value=[])

        with (
            patch("testagent.db.engine.get_session", return_value=_make_mock_session_ctx(AsyncMock())),
            patch("testagent.db.repository.RetrievalTraceRepository", return_value=mock_repo),
        ):
            result = runner.invoke(app, ["memory", "trace", "com.example.app"])
            assert result.exit_code == 0
            assert "No traces found" in result.stdout

    def test_trace_custom_days(self) -> None:
        mock_repo = MagicMock()
        mock_repo.get_by_app_id = AsyncMock(return_value=[])

        with (
            patch("testagent.db.engine.get_session", return_value=_make_mock_session_ctx(AsyncMock())),
            patch("testagent.db.repository.RetrievalTraceRepository", return_value=mock_repo),
        ):
            result = runner.invoke(app, ["memory", "trace", "com.example.app", "--days", "30"])
            assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

class TestMemoryRegistration:
    def test_memory_in_help(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "memory" in result.stdout

    def test_memory_subcommands_in_help(self) -> None:
        result = runner.invoke(app, ["memory", "--help"])
        assert result.exit_code == 0
        assert "list-patterns" in result.stdout
        assert "approve" in result.stdout
        assert "reject" in result.stdout
        assert "add-pattern" in result.stdout
        assert "search" in result.stdout
        assert "trace" in result.stdout
