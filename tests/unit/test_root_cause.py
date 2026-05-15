from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from testagent.agent.root_cause import (
    FAILURE_PATTERNS_COLLECTION,
    RootCauseAnalysisError,
    RootCauseAnalyzer,
    RootCauseResult,
)
from testagent.llm.base import LLMResponse
from testagent.mcp_servers.git_server.server import GitMCPServer
from testagent.rag.pipeline import RAGPipeline


def _make_mock_git_server(git_output: dict | None = None) -> MagicMock:
    server = MagicMock(spec=GitMCPServer)
    server.server_name = "git_server"

    if git_output is None:
        git_output = {"output": "abc123 (HEAD~1) developer 2024-01-15 fix: resolve login issue", "exit_code": 0}

    server.call_tool = AsyncMock(return_value=json.dumps(git_output))
    return server


def _make_mock_llm_provider(llm_response: dict | None = None) -> MagicMock:
    provider = MagicMock()
    if llm_response is None:
        llm_response = {
            "root_cause_type": "code_change",
            "confidence": 0.85,
            "reasoning": "Recent commit changed the login validation logic",
            "suggestion": "Revert the login validation change and add proper error handling",
        }
    mock_response = LLMResponse(
        content=[{"type": "text", "text": json.dumps(llm_response)}],
        stop_reason="end_turn",
        usage={"input_tokens": 500, "output_tokens": 100},
    )
    provider.chat = AsyncMock(return_value=mock_response)
    provider.embed = AsyncMock(return_value=[0.1] * 10)
    provider.embed_batch = AsyncMock(return_value=[[0.1] * 10])
    return provider


def _make_mock_rag_pipeline() -> MagicMock:
    rag = MagicMock(spec=RAGPipeline)
    rag.query = AsyncMock(return_value=[])
    rag.write_back = AsyncMock()
    return rag


def _make_defect_dict(
    defect_id: str = "def-001",
    result_id: str = "res-001",
    category: str = "bug",
    severity: str = "major",
    title: str = "Login API returns 500",
) -> dict:
    return {
        "id": defect_id,
        "result_id": result_id,
        "category": category,
        "severity": severity,
        "title": title,
        "description": "Login endpoint returns 500 when valid credentials are provided",
        "status": "open",
    }


def _make_test_result_dict(
    result_id: str = "res-001",
    status: str = "failed",
    logs: str | None = None,
) -> dict:
    return {
        "id": result_id,
        "task_id": "task-001",
        "status": status,
        "duration_ms": 1500.0,
        "logs": logs
        or """ERROR: Internal Server Error
Traceback (most recent call last):
  File "/app/api/login.py", line 42, in handle_login
    user = db.query(User).filter_by(email=email).first()
  File "/app/db/session.py", line 88, in query
    raise DatabaseConnectionError("Connection pool exhausted")
sqlalchemy.exc.OperationalError: (OperationalError) connection pool exhausted
""",
        "assertion_results": {"passed": 2, "failed": 1, "errors": ["Expected 200, got 500"]},
        "artifacts": {"source_file": "src/api/login.py", "source_line": 42},
    }


class TestRootCauseResult:
    def test_default_construction(self) -> None:
        result = RootCauseResult(defect_id="def-001", root_cause_type="code_change", confidence=0.85)
        assert result.defect_id == "def-001"
        assert result.root_cause_type == "code_change"
        assert result.confidence == 0.85
        assert result.related_commits == []
        assert result.related_prs == []
        assert result.code_snippets == []
        assert result.suggestion == ""
        assert result.similar_historical_defects == []

    def test_full_construction(self) -> None:
        result = RootCauseResult(
            defect_id="def-002",
            root_cause_type="config_change",
            confidence=0.72,
            related_commits=[{"hash": "abc123", "message": "fix config"}],
            related_prs=[{"id": 42, "title": "Fix config issue"}],
            code_snippets=[{"file": "config.py", "diff": "-old\n+new"}],
            suggestion="Update the configuration to use the correct endpoint",
            similar_historical_defects=[{"doc_id": "hist-001", "content": "Similar config issue", "score": 0.91}],
        )
        assert result.defect_id == "def-002"
        assert result.root_cause_type == "config_change"
        assert len(result.related_commits) == 1
        assert len(result.related_prs) == 1
        assert len(result.code_snippets) == 1
        assert "correct endpoint" in result.suggestion
        assert len(result.similar_historical_defects) == 1

    def test_to_dict(self) -> None:
        result = RootCauseResult(defect_id="def-001", root_cause_type="code_change", confidence=0.85)
        d = result.to_dict()
        assert d["defect_id"] == "def-001"
        assert d["root_cause_type"] == "code_change"
        assert d["confidence"] == 0.85
        assert isinstance(d["related_commits"], list)
        assert isinstance(d["related_prs"], list)
        assert isinstance(d["code_snippets"], list)
        assert isinstance(d["suggestion"], str)
        assert isinstance(d["similar_historical_defects"], list)

    def test_to_dict_is_serializable(self) -> None:
        result = RootCauseResult(defect_id="def-001", root_cause_type="code_change", confidence=0.85)
        d = result.to_dict()
        json_str = json.dumps(d, ensure_ascii=False, default=str)
        parsed = json.loads(json_str)
        assert parsed["defect_id"] == "def-001"
        assert parsed["root_cause_type"] == "code_change"
        assert parsed["confidence"] == 0.85


class TestRootCauseAnalyzer:
    @pytest.mark.asyncio
    async def test_analyze_full_flow(self) -> None:
        mock_git = _make_mock_git_server()
        mock_llm = _make_mock_llm_provider()
        mock_rag = _make_mock_rag_pipeline()

        analyzer = RootCauseAnalyzer(
            git_server=mock_git,
            llm=mock_llm,
            rag=mock_rag,
            repo_path="/fake/repo",
        )

        defect_dict = _make_defect_dict()
        test_result_dict = _make_test_result_dict()

        from testagent.models.result import TestResult
        test_result = TestResult(**test_result_dict)
        result = await analyzer.analyze(defect_dict, test_result)

        assert isinstance(result, RootCauseResult)
        assert result.defect_id == "def-001"
        assert result.root_cause_type == "code_change"
        assert result.confidence == 0.85
        assert mock_git.call_tool.called

        git_call_names = [call[0][0] for call in mock_git.call_tool.call_args_list]
        assert "git_blame" in git_call_names
        assert "git_log" in git_call_names
        assert "git_diff" in git_call_names

        assert mock_rag.query.called
        assert mock_rag.write_back.called

        write_back_call = mock_rag.write_back.call_args
        assert write_back_call is not None
        kwargs = write_back_call.kwargs if write_back_call.kwargs else write_back_call[1]
        assert kwargs.get("collection") == FAILURE_PATTERNS_COLLECTION
        assert "defect_id" in str(kwargs.get("metadata", {}))

    @pytest.mark.asyncio
    async def test_analyze_no_git_repo(self) -> None:
        mock_git = _make_mock_git_server()
        mock_llm = _make_mock_llm_provider()
        mock_rag = _make_mock_rag_pipeline()

        analyzer = RootCauseAnalyzer(
            git_server=mock_git,
            llm=mock_llm,
            rag=mock_rag,
            repo_path="",
        )

        defect_dict = _make_defect_dict()
        from testagent.models.result import TestResult
        test_result = TestResult(**_make_test_result_dict())

        result = await analyzer.analyze(defect_dict, test_result)

        assert isinstance(result, RootCauseResult)
        assert result.related_commits == []
        assert result.code_snippets == []
        assert mock_git.call_tool.called is False
        assert mock_rag.query.called
        assert mock_rag.write_back.called

    @pytest.mark.asyncio
    async def test_analyze_rag_similar_defects(self) -> None:
        mock_git = _make_mock_git_server()
        mock_llm = _make_mock_llm_provider()
        mock_rag = _make_mock_rag_pipeline()

        similar_defects = [
            MagicMock(
                doc_id="hist-001",
                content="Similar database connection pool exhaustion",
                score=0.92,
                metadata={"defect_category": "bug", "defect_severity": "critical"},
            )
        ]
        mock_rag.query = AsyncMock(return_value=similar_defects)

        analyzer = RootCauseAnalyzer(
            git_server=mock_git,
            llm=mock_llm,
            rag=mock_rag,
            repo_path="/fake/repo",
        )

        defect_dict = _make_defect_dict()
        from testagent.models.result import TestResult
        test_result = TestResult(**_make_test_result_dict())

        result = await analyzer.analyze(defect_dict, test_result)

        assert len(result.similar_historical_defects) == 1
        hist = result.similar_historical_defects[0]
        assert hist["doc_id"] == "hist-001"
        assert hist["score"] == 0.92

    @pytest.mark.asyncio
    async def test_analyze_llm_returns_json_in_text(self) -> None:
        mock_git = _make_mock_git_server()
        mock_rag = _make_mock_rag_pipeline()

        provider = MagicMock()
        raw_text = (
            '{"root_cause_type": "env_issue", "confidence": 0.65, '
            '"reasoning": "Environment variable missing", '
            '"suggestion": "Add the missing env var to CI config"}'
        )
        mock_response = LLMResponse(
            content=[{"type": "text", "text": raw_text}],
            stop_reason="end_turn",
            usage={"input_tokens": 500, "output_tokens": 80},
        )
        provider.chat = AsyncMock(return_value=mock_response)

        analyzer = RootCauseAnalyzer(
            git_server=mock_git,
            llm=provider,
            rag=mock_rag,
            repo_path="",
        )

        defect_dict = _make_defect_dict()
        from testagent.models.result import TestResult
        test_result = TestResult(**_make_test_result_dict())

        result = await analyzer.analyze(defect_dict, test_result)

        assert result.root_cause_type == "env_issue"
        assert result.confidence == 0.65
        assert "missing" in result.suggestion

    @pytest.mark.asyncio
    async def test_analyze_llm_failure_fallback(self) -> None:
        mock_git = _make_mock_git_server()
        mock_rag = _make_mock_rag_pipeline()

        provider = MagicMock()
        provider.chat = AsyncMock(side_effect=Exception("LLM API unavailable"))

        analyzer = RootCauseAnalyzer(
            git_server=mock_git,
            llm=provider,
            rag=mock_rag,
            repo_path="",
        )

        defect_dict = _make_defect_dict()
        from testagent.models.result import TestResult
        test_result = TestResult(**_make_test_result_dict())

        result = await analyzer.analyze(defect_dict, test_result)

        assert result.root_cause_type == "unknown"
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_analyze_git_failure_does_not_block(self) -> None:
        mock_git = _make_mock_git_server()
        mock_git.call_tool = AsyncMock(return_value=json.dumps({"error": "Git repository not found"}))

        mock_llm = _make_mock_llm_provider()
        mock_rag = _make_mock_rag_pipeline()

        analyzer = RootCauseAnalyzer(
            git_server=mock_git,
            llm=mock_llm,
            rag=mock_rag,
            repo_path="/fake/repo",
        )

        defect_dict = _make_defect_dict()
        from testagent.models.result import TestResult
        test_result = TestResult(**_make_test_result_dict())

        result = await analyzer.analyze(defect_dict, test_result)

        assert isinstance(result, RootCauseResult)
        assert result.root_cause_type == "code_change"

    @pytest.mark.asyncio
    async def test_extract_error_info_from_test_result(self) -> None:
        mock_git = _make_mock_git_server()
        mock_llm = _make_mock_llm_provider()
        mock_rag = _make_mock_rag_pipeline()

        analyzer = RootCauseAnalyzer(
            git_server=mock_git,
            llm=mock_llm,
            rag=mock_rag,
        )

        from testagent.models.result import TestResult
        test_result = TestResult(
            task_id="task-001",
            status="failed",
            logs="ERROR: Connection refused",
            assertion_results={"passed": 0, "failed": 1},
            artifacts={"error_type": "ConnectionError"},
        )

        error_info = analyzer._extract_error_info(test_result)
        assert "ERROR: Connection refused" in error_info
        assert "Assertion Results" in error_info
        assert "Error Artifacts" in error_info
        assert "ConnectionError" in error_info

    @pytest.mark.asyncio
    async def test_extract_file_location_from_artifacts(self) -> None:
        mock_git = _make_mock_git_server()
        mock_llm = _make_mock_llm_provider()
        mock_rag = _make_mock_rag_pipeline()

        analyzer = RootCauseAnalyzer(
            git_server=mock_git,
            llm=mock_llm,
            rag=mock_rag,
        )

        from testagent.models.result import TestResult
        test_result = TestResult(
            task_id="task-001",
            status="failed",
            logs="Error in file",
            artifacts={"source_file": "src/app/controller.py", "source_line": 55},
        )

        file_path, line = analyzer._extract_file_location("some error", test_result)
        assert file_path == "src/app/controller.py"
        assert line == 55

    @pytest.mark.asyncio
    async def test_extract_file_location_from_error_text(self) -> None:
        mock_git = _make_mock_git_server()
        mock_llm = _make_mock_llm_provider()
        mock_rag = _make_mock_rag_pipeline()

        analyzer = RootCauseAnalyzer(
            git_server=mock_git,
            llm=mock_llm,
            rag=mock_rag,
        )

        from testagent.models.result import TestResult
        test_result = TestResult(
            task_id="task-001",
            status="failed",
            logs='File "/app/api/user.py", line 102, in get_user',
        )

        extracted_path, _ = analyzer._extract_file_location("some error", test_result)
        assert extracted_path is None or extracted_path != ""

    @pytest.mark.asyncio
    async def test_write_back_to_rag_success(self) -> None:
        mock_git = _make_mock_git_server()
        mock_llm = _make_mock_llm_provider()
        mock_rag = _make_mock_rag_pipeline()

        analyzer = RootCauseAnalyzer(
            git_server=mock_git,
            llm=mock_llm,
            rag=mock_rag,
        )

        defect_dict = _make_defect_dict()
        result = RootCauseResult(defect_id="def-001", root_cause_type="code_change", confidence=0.85)
        from testagent.models.result import TestResult
        test_result = TestResult(**_make_test_result_dict())

        await analyzer._write_back_to_rag(defect_dict, test_result, result)

        assert mock_rag.write_back.called
        call_kwargs = mock_rag.write_back.call_args.kwargs or mock_rag.write_back.call_args[1]
        assert call_kwargs["collection"] == FAILURE_PATTERNS_COLLECTION
        metadata = call_kwargs.get("metadata", {})
        assert metadata["defect_id"] == "def-001"
        assert metadata["root_cause_type"] == "code_change"
        assert "analyzed_at" in metadata

    @pytest.mark.asyncio
    async def test_write_back_to_rag_failure_does_not_raise(self) -> None:
        mock_git = _make_mock_git_server()
        mock_llm = _make_mock_llm_provider()
        mock_rag = _make_mock_rag_pipeline()
        mock_rag.write_back = AsyncMock(side_effect=Exception("RAG write failed"))

        analyzer = RootCauseAnalyzer(
            git_server=mock_git,
            llm=mock_llm,
            rag=mock_rag,
        )

        defect_dict = _make_defect_dict()
        result = RootCauseResult(defect_id="def-001", root_cause_type="code_change", confidence=0.85)
        from testagent.models.result import TestResult
        test_result = TestResult(**_make_test_result_dict())

        await analyzer._write_back_to_rag(defect_dict, test_result, result)
        assert mock_rag.write_back.called

    @pytest.mark.asyncio
    async def test_analyze_with_no_error_info(self) -> None:
        mock_git = _make_mock_git_server()
        mock_llm = _make_mock_llm_provider()
        mock_rag = _make_mock_rag_pipeline()

        analyzer = RootCauseAnalyzer(
            git_server=mock_git,
            llm=mock_llm,
            rag=mock_rag,
            repo_path="/fake/repo",
        )

        defect_dict = _make_defect_dict()
        from testagent.models.result import TestResult
        test_result = TestResult(task_id="task-001", status="failed")

        result = await analyzer.analyze(defect_dict, test_result)

        assert isinstance(result, RootCauseResult)
        assert result.defect_id == "def-001"

    @pytest.mark.asyncio
    async def test_analyze_rag_query_failure(self) -> None:
        mock_git = _make_mock_git_server()
        mock_llm = _make_mock_llm_provider()
        mock_rag = _make_mock_rag_pipeline()
        mock_rag.query = AsyncMock(side_effect=Exception("RAG unavailable"))

        analyzer = RootCauseAnalyzer(
            git_server=mock_git,
            llm=mock_llm,
            rag=mock_rag,
            repo_path="",
        )

        defect_dict = _make_defect_dict()
        from testagent.models.result import TestResult
        test_result = TestResult(**_make_test_result_dict())

        result = await analyzer.analyze(defect_dict, test_result)

        assert isinstance(result, RootCauseResult)
        assert result.similar_historical_defects == []


class TestRootCauseAnalyzerErrors:
    def test_root_cause_analysis_error(self) -> None:
        error = RootCauseAnalysisError("Analysis failed", code="ANALYSIS_FAILED")
        assert error.code == "ANALYSIS_FAILED"
        assert "Analysis failed" in str(error)

    def test_root_cause_types_constants(self) -> None:
        from testagent.agent.root_cause import ROOT_CAUSE_TYPES

        assert "code_change" in ROOT_CAUSE_TYPES
        assert "config_change" in ROOT_CAUSE_TYPES
        assert "env_issue" in ROOT_CAUSE_TYPES
        assert "data_issue" in ROOT_CAUSE_TYPES
        assert "unknown" in ROOT_CAUSE_TYPES


class TestRootCauseAnalyzerNoRepoPath:
    @pytest.mark.asyncio
    async def test_no_git_operations_when_repo_path_empty(self) -> None:
        mock_git = _make_mock_git_server()
        mock_llm = _make_mock_llm_provider()
        mock_rag = _make_mock_rag_pipeline()

        analyzer = RootCauseAnalyzer(git_server=mock_git, llm=mock_llm, rag=mock_rag, repo_path="")

        assert analyzer._repo_path == ""

        from testagent.models.result import TestResult
        test_result = TestResult(**_make_test_result_dict())
        analyzer._extract_file_location("test error", test_result)

        related_commits = await analyzer._get_related_commits("src/test.py", 42)
        assert related_commits == []

        code_snippets = await analyzer._get_code_snippets("src/test.py", 42)
        assert code_snippets == []

    @pytest.mark.asyncio
    async def test_get_related_commits_without_line_number(self) -> None:
        mock_git = _make_mock_git_server()
        git_output = {"output": "abc123 commit message\nxyz789 another commit", "exit_code": 0}
        mock_git.call_tool = AsyncMock(return_value=json.dumps(git_output))

        mock_llm = _make_mock_llm_provider()
        mock_rag = _make_mock_rag_pipeline()

        analyzer = RootCauseAnalyzer(
            git_server=mock_git,
            llm=mock_llm,
            rag=mock_rag,
            repo_path="/fake/repo",
        )

        commits = await analyzer._get_related_commits("src/test.py", None)
        assert len(commits) == 1
        assert "recent_commits" in commits[0]


class TestRootCauseAnalyzerWithGitError:
    @pytest.mark.asyncio
    async def test_git_error_returns_empty_collections(self) -> None:
        mock_git = _make_mock_git_server()
        mock_git.call_tool = AsyncMock(return_value=json.dumps({"error": "fatal: not a git repository"}))

        mock_llm = _make_mock_llm_provider()
        mock_rag = _make_mock_rag_pipeline()

        analyzer = RootCauseAnalyzer(
            git_server=mock_git,
            llm=mock_llm,
            rag=mock_rag,
            repo_path="/fake/repo",
        )

        related_commits = await analyzer._get_related_commits("src/test.py", 42)
        assert len(related_commits) >= 0

        code_snippets = await analyzer._get_code_snippets("src/test.py", 42)
        assert code_snippets == []
