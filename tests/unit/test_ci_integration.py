from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

if TYPE_CHECKING:
    from collections.abc import Generator

import pytest
from typer.testing import CliRunner

from testagent.cli.junit import generate_junit_xml
from testagent.cli.main import _write_junit_report, app


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


class TestCiCommand:
    """Test the CI command integration."""

    def test_ci_help(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["ci", "--help"])
        assert result.exit_code == 0
        assert "--exit-code" in result.stdout
        assert "--junit" in result.stdout
        assert "--timeout" in result.stdout
        assert "--env" in result.stdout
        assert "--url" in result.stdout

    def test_ci_negative_timeout_fails(self, runner: CliRunner) -> None:
        """Typer min=1 validation returns exit code 2."""
        result = runner.invoke(app, ["ci", "api_smoke_test", "--timeout", "-1"])
        assert result.exit_code == 2

    def test_ci_zero_timeout_fails(self, runner: CliRunner) -> None:
        """Typer min=1 validation returns exit code 2."""
        result = runner.invoke(app, ["ci", "api_smoke_test", "--timeout", "0"])
        assert result.exit_code == 2

    def test_ci_run_session_called(self, runner: CliRunner) -> None:
        """Verify run_session is called with expected arguments."""
        mock_results = {
            "session_id": "test-123",
            "status": "completed",
            "tasks": [],
            "duration": "0.1s",
        }

        with (
            patch("testagent.gateway.session.run_session", new_callable=AsyncMock) as mock_run,
            patch("testagent.cli.main._write_junit_report") as mock_junit,
        ):
            mock_run.return_value = mock_results
            result = runner.invoke(app, ["ci", "api_smoke_test"])

            assert result.exit_code == 0
            mock_run.assert_awaited_once_with(skill_name="api_smoke_test", env="ci", url=None)
            mock_junit.assert_called_once()

    def test_ci_exit_code_on_failure(self, runner: CliRunner) -> None:
        mock_results = {
            "session_id": "test-123",
            "status": "completed",
            "tasks": [
                {"name": "Test 1", "status": "passed", "duration": "1.0s"},
                {"name": "Test 2", "status": "failed", "duration": "0.5s", "error": "Assertion failed"},
            ],
            "duration": "1.5s",
        }

        with (
            patch("testagent.gateway.session.run_session", new_callable=AsyncMock) as mock_run,
            patch("testagent.cli.main._write_junit_report") as mock_junit,
        ):
            mock_run.return_value = mock_results
            result = runner.invoke(app, ["ci", "api_smoke_test", "--exit-code"])

            assert result.exit_code == 1
            mock_junit.assert_called_once()

    def test_ci_exit_code_all_passed(self, runner: CliRunner) -> None:
        mock_results = {
            "session_id": "test-456",
            "status": "completed",
            "tasks": [
                {"name": "Test 1", "status": "passed", "duration": "1.0s"},
                {"name": "Test 2", "status": "passed", "duration": "0.8s"},
            ],
            "duration": "1.8s",
        }

        with (
            patch("testagent.gateway.session.run_session", new_callable=AsyncMock) as mock_run,
            patch("testagent.cli.main._write_junit_report") as mock_junit,
        ):
            mock_run.return_value = mock_results
            result = runner.invoke(app, ["ci", "api_smoke_test", "--exit-code"])

            assert result.exit_code == 0
            mock_junit.assert_called_once()

    def test_ci_no_exit_code_with_failures(self, runner: CliRunner) -> None:
        mock_results = {
            "session_id": "test-789",
            "status": "completed",
            "tasks": [
                {"name": "Test 1", "status": "failed", "duration": "0.5s"},
            ],
            "duration": "0.5s",
        }

        with (
            patch("testagent.gateway.session.run_session", new_callable=AsyncMock) as mock_run,
            patch("testagent.cli.main._write_junit_report") as mock_junit,
        ):
            mock_run.return_value = mock_results
            result = runner.invoke(app, ["ci", "api_smoke_test"])

            assert result.exit_code == 0
            mock_junit.assert_called_once()

    def test_ci_junit_report_written(self, runner: CliRunner, temp_dir: Path) -> None:
        mock_results = {
            "session_id": "test-101",
            "status": "completed",
            "tasks": [
                {"name": "Test 1", "status": "passed", "duration": "1.0s"},
                {"name": "Test 2", "status": "failed", "duration": "0.5s", "error": "Assertion failed"},
            ],
            "duration": "1.5s",
        }

        junit_path = temp_dir / "ci-results.xml"

        with patch("testagent.gateway.session.run_session", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = mock_results
            result = runner.invoke(
                app,
                ["ci", "api_smoke_test", "--junit", str(junit_path), "--exit-code"],
            )

            assert result.exit_code == 1
            assert junit_path.exists()
            content = junit_path.read_text("utf-8")
            assert 'name="Test 1"' in content
            assert 'name="Test 2"' in content
            assert "<failure" in content

    def test_ci_junit_report_not_written_without_flag(self, runner: CliRunner) -> None:
        mock_results = {
            "session_id": "test-202",
            "status": "completed",
            "tasks": [],
            "duration": "0.1s",
        }

        with (
            patch("testagent.gateway.session.run_session", new_callable=AsyncMock) as mock_run,
            patch("testagent.cli.main._write_junit_report") as mock_junit,
        ):
            mock_run.return_value = mock_results
            result = runner.invoke(app, ["ci", "api_smoke_test"])

            assert result.exit_code == 0
            mock_junit.assert_called_once_with([], None)

    def test_ci_with_env(self, runner: CliRunner) -> None:
        mock_results: dict[str, object] = {
            "session_id": "test-303",
            "status": "completed",
            "tasks": [],
            "duration": "0.1s",
        }

        with (
            patch("testagent.gateway.session.run_session", new_callable=AsyncMock) as mock_run,
            patch("testagent.cli.main._write_junit_report"),
        ):
            mock_run.return_value = mock_results
            result = runner.invoke(app, ["ci", "api_smoke_test", "--env", "staging"])

            assert result.exit_code == 0
            mock_run.assert_awaited_once_with(skill_name="api_smoke_test", env="staging", url=None)

    def test_ci_with_url(self, runner: CliRunner) -> None:
        mock_results: dict[str, object] = {
            "session_id": "test-404",
            "status": "completed",
            "tasks": [],
            "duration": "0.1s",
        }

        with (
            patch("testagent.gateway.session.run_session", new_callable=AsyncMock) as mock_run,
            patch("testagent.cli.main._write_junit_report"),
        ):
            mock_run.return_value = mock_results
            result = runner.invoke(
                app,
                ["ci", "api_smoke_test", "--url", "https://staging.example.com"],
            )

            assert result.exit_code == 0
            mock_run.assert_awaited_once_with(skill_name="api_smoke_test", env="ci", url="https://staging.example.com")

    def test_ci_with_url_and_env(self, runner: CliRunner) -> None:
        mock_results: dict[str, object] = {
            "session_id": "test-505",
            "status": "completed",
            "tasks": [],
            "duration": "0.1s",
        }

        with (
            patch("testagent.gateway.session.run_session", new_callable=AsyncMock) as mock_run,
            patch("testagent.cli.main._write_junit_report"),
        ):
            mock_run.return_value = mock_results
            result = runner.invoke(
                app,
                [
                    "ci",
                    "api_smoke_test",
                    "--url",
                    "https://prod.example.com",
                    "--env",
                    "production",
                ],
            )

            assert result.exit_code == 0
            mock_run.assert_awaited_once_with(
                skill_name="api_smoke_test", env="production", url="https://prod.example.com"
            )

    def test_ci_timeout_exception(self, runner: CliRunner, temp_dir: Path) -> None:
        junit_path = temp_dir / "timeout-results.xml"

        with patch("testagent.gateway.session.run_session", new_callable=AsyncMock) as mock_run:
            mock_run.side_effect = TimeoutError("Simulated timeout")
            result = runner.invoke(
                app,
                ["ci", "api_smoke_test", "--timeout", "10", "--junit", str(junit_path)],
            )

            assert result.exit_code == 1
            assert "timed out" in result.stdout
            assert junit_path.exists()
            content = junit_path.read_text("utf-8")
            assert 'name="api_smoke_test"' in content
            assert "Global timeout exceeded" in content


class TestJunitXmlGeneration:
    """Test JUnit XML report generation."""

    def test_generate_empty_tasks(self) -> None:
        xml = generate_junit_xml([])
        assert 'tests="0"' in xml
        assert 'failures="0"' in xml
        assert 'errors="0"' in xml

    def test_generate_all_passed(self) -> None:
        tasks = [
            {"name": "test_a", "status": "passed", "duration": "1.2"},
            {"name": "test_b", "status": "passed", "duration": "0.8"},
        ]
        xml = generate_junit_xml(tasks)
        assert 'tests="2"' in xml
        assert 'failures="0"' in xml
        assert 'name="test_a"' in xml
        assert 'name="test_b"' in xml
        assert "<failure" not in xml

    def test_generate_with_failures(self) -> None:
        tasks = [
            {"name": "test_a", "status": "passed", "duration": "1.0"},
            {"name": "test_b", "status": "failed", "duration": "0.5", "error": "Expected 200, got 500"},
        ]
        xml = generate_junit_xml(tasks)
        assert 'failures="1"' in xml
        assert "<failure" in xml
        assert "Expected 200, got 500" in xml

    def test_generate_with_errors(self) -> None:
        tasks = [
            {"name": "test_a", "status": "error", "duration": "2.0", "error": "Connection refused"},
        ]
        xml = generate_junit_xml(tasks)
        assert 'errors="1"' in xml
        assert "<error" in xml

    def test_generate_with_skipped(self) -> None:
        tasks = [
            {"name": "test_a", "status": "passed", "duration": "1.0"},
            {"name": "test_b", "status": "skipped", "duration": "0.0"},
        ]
        xml = generate_junit_xml(tasks)
        assert 'skipped="1"' in xml
        assert "<skipped" in xml

    def test_generate_with_flaky(self) -> None:
        tasks = [
            {"name": "test_a", "status": "flaky", "duration": "2.0"},
        ]
        xml = generate_junit_xml(tasks)
        assert 'skipped="1"' in xml
        assert "<skipped" in xml

    def test_generate_custom_suite_name(self) -> None:
        tasks = [{"name": "test_a", "status": "passed", "duration": "1.0"}]
        xml = generate_junit_xml(tasks, suite_name="custom_suite")
        assert 'name="custom_suite"' in xml

    def test_generate_task_without_error_on_failure(self) -> None:
        tasks = [
            {"name": "test_a", "status": "failed", "duration": "0.5"},
        ]
        xml = generate_junit_xml(tasks)
        assert "<failure" in xml
        assert "No error details" in xml

    def test_generate_unknown_status(self) -> None:
        tasks = [
            {"name": "test_a", "status": "unknown", "duration": "1.0"},
        ]
        xml = generate_junit_xml(tasks)
        assert 'name="test_a"' in xml
        assert "<failure" not in xml
        assert "<error" not in xml
        assert "<skipped" not in xml

    def test_generate_missing_fields(self) -> None:
        tasks: list[dict[str, object]] = [
            {},
            {"name": "named_task"},
        ]
        xml = generate_junit_xml(tasks)
        assert 'name="unnamed"' in xml
        assert 'name="named_task"' in xml


class TestWriteJunitReport:
    """Test JUnit report writing helper."""

    def test_write_report(self, temp_dir: Path) -> None:
        tasks = [{"name": "test_a", "status": "passed", "duration": "1.0"}]
        path = temp_dir / "report.xml"
        _write_junit_report(tasks, path)
        assert path.exists()
        content = path.read_text("utf-8")
        assert 'name="test_a"' in content

    def test_write_report_none_path(self) -> None:
        _write_junit_report([{"name": "test_a", "status": "passed", "duration": "1.0"}], None)

    def test_write_report_overwrites_existing(self, temp_dir: Path) -> None:
        path = temp_dir / "report.xml"
        path.write_text("old content", encoding="utf-8")
        tasks = [{"name": "test_b", "status": "failed", "duration": "0.5"}]
        _write_junit_report(tasks, path)
        content = path.read_text("utf-8")
        assert "old content" not in content
        assert 'name="test_b"' in content


class TestMcpConfigTemplate:
    """Test MCP configuration template validity."""

    def test_mcp_template_valid_json(self) -> None:
        template_path = Path("configs/mcp.json.template")
        content = template_path.read_text("utf-8")
        config = json.loads(content)
        assert "meta" in config
        assert "servers" in config
        assert config["meta"]["version"] == "1.0"

    def test_mcp_template_has_required_servers(self) -> None:
        template_path = Path("configs/mcp.json.template")
        content = template_path.read_text("utf-8")
        config = json.loads(content)
        servers = config["servers"]
        required_servers = [
            "api_server",
            "playwright_server",
            "jira_server",
            "git_server",
            "database_server",
        ]
        for server in required_servers:
            assert server in servers, f"Missing server: {server}"
            assert "command" in servers[server]
            assert "args" in servers[server]

    def test_mcp_template_jira_uses_env_var(self) -> None:
        template_path = Path("configs/mcp.json.template")
        content = template_path.read_text("utf-8")
        config = json.loads(content)
        jira_env = config["servers"]["jira_server"]["env"]
        assert "${JIRA_API_TOKEN}" in jira_env["MCP_JIRA_API_TOKEN"]


class TestRagConfigTemplate:
    """Test RAG configuration template validity."""

    def test_rag_template_required_collections(self) -> None:
        import yaml

        template_path = Path("configs/rag_config.yaml.template")
        content = template_path.read_text("utf-8")
        config = yaml.safe_load(content)
        collections = config["rag"]["collections"]
        required_collections = [
            "req_docs",
            "api_docs",
            "defect_history",
            "test_reports",
            "locator_library",
            "failure_patterns",
        ]
        for coll in required_collections:
            assert coll in collections, f"Missing collection: {coll}"
            assert "description" in collections[coll]
            assert "index_strategy" in collections[coll]
            assert "access_roles" in collections[coll]

    def test_rag_template_embedding_config(self) -> None:
        import yaml

        template_path = Path("configs/rag_config.yaml.template")
        content = template_path.read_text("utf-8")
        config = yaml.safe_load(content)
        embedding = config["rag"]["embedding"]
        assert embedding["provider"] in ("openai", "local")
        assert "model" in embedding
        assert "dimensions" in embedding

    def test_rag_template_retrieval_config(self) -> None:
        import yaml

        template_path = Path("configs/rag_config.yaml.template")
        content = template_path.read_text("utf-8")
        config = yaml.safe_load(content)
        retrieval = config["rag"]["retrieval"]
        assert "top_k" in retrieval
        assert "rrf_k" in retrieval
        assert retrieval["rrf_k"] == 60
