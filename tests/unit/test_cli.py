from __future__ import annotations

import builtins
import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from collections.abc import Generator

import pytest
from typer.testing import CliRunner

from testagent.cli.main import app
from testagent.cli.output import RichOutput


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def temp_project() -> Generator[Path, None, None]:
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


class TestCliCommands:
    """Test CLI commands registration and execution."""

    def test_help_output(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "testagent" in result.stdout
        assert "init" in result.stdout
        assert "run" in result.stdout
        assert "chat" in result.stdout
        assert "ci" in result.stdout
        assert "serve" in result.stdout
        assert "skill" in result.stdout
        assert "mcp" in result.stdout
        assert "rag-index" in result.stdout
        assert "rag-query" in result.stdout

    def test_init_creates_project(self, runner: CliRunner, temp_project: Path) -> None:
        with patch("testagent.cli.main.Path.cwd", return_value=temp_project):
            project_name = "my-test-project"
            result = runner.invoke(app, ["init", project_name])
            assert result.exit_code == 0
            assert project_name in result.stdout

            project_dir = temp_project / project_name
            assert project_dir.exists()
            assert (project_dir / "test-plans").exists()
            assert (project_dir / "config").exists()
            assert (project_dir / "testagent.json").exists()

            config = json.loads((project_dir / "testagent.json").read_text("utf-8"))
            assert config["project"] == project_name
            assert config["type"] == "api"

    def test_init_with_type(self, runner: CliRunner, temp_project: Path) -> None:
        with patch("testagent.cli.main.Path.cwd", return_value=temp_project):
            result = runner.invoke(app, ["init", "web-app", "--type", "web"])
            assert result.exit_code == 0
            config = json.loads((temp_project / "web-app" / "testagent.json").read_text("utf-8"))
            assert config["type"] == "web"

    def test_init_existing_project_fails(self, runner: CliRunner, temp_project: Path) -> None:
        existing = temp_project / "exists"
        existing.mkdir()
        with patch("testagent.cli.main.Path.cwd", return_value=temp_project):
            result = runner.invoke(app, ["init", "exists"])
            assert result.exit_code == 1
            assert "already exists" in result.stdout

    def test_run_requires_skill_or_plan(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["run"])
        assert result.exit_code == 1
        assert "--skill" in result.stdout or "--plan" in result.stdout

    def test_run_with_skill_requires_session_module(self, runner: CliRunner) -> None:
        real_import = builtins.__import__

        def _block_session(name: str, *args: object, **kwargs: object) -> object:
            if name == "testagent.gateway.session":
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        with patch("builtins.__import__", side_effect=_block_session):
            result = runner.invoke(app, ["run", "--skill", "api_smoke_test"])
        assert result.exit_code == 1
        assert "Session execution module not available" in result.stdout

    def test_run_with_nonexistent_plan_fails(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["run", "--plan", "/nonexistent/plan.json"])
        assert result.exit_code == 1
        assert "not found" in result.stdout

    def test_ci_requires_session_module(self, runner: CliRunner) -> None:
        real_import = builtins.__import__

        def _block_session(name: str, *args: object, **kwargs: object) -> object:
            if name == "testagent.gateway.session":
                raise ImportError(f"No module named '{name}'")
            return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

        with patch("builtins.__import__", side_effect=_block_session):
            result = runner.invoke(app, ["ci", "api_smoke_test"])
        assert result.exit_code == 1
        assert "Session execution module not available" in result.stdout

    def test_serve_registered(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["serve", "--help"])
        assert result.exit_code == 0
        assert "Start the TestAgent Gateway server" in result.stdout

    def test_skill_help(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["skill", "--help"])
        assert result.exit_code == 0
        assert "list" in result.stdout
        assert "create" in result.stdout

    def test_skill_list_empty(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["skill", "list"])
        assert result.exit_code == 0
        assert "No skills registered" in result.stdout

    def test_skill_create_unknown_template(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["skill", "create", "--template", "nonexistent"])
        assert result.exit_code == 1
        assert "Unknown template" in result.stdout

    def test_skill_create_from_template(self, runner: CliRunner, temp_project: Path) -> None:
        result = runner.invoke(app, ["skill", "create", "--template", "api_test", "--output", str(temp_project)])
        assert result.exit_code == 0
        assert "Created skill" in result.stdout
        assert (temp_project / "api_test.md").exists()

    def test_mcp_help(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["mcp", "--help"])
        assert result.exit_code == 0
        assert "add" in result.stdout
        assert "list" in result.stdout
        assert "health" in result.stdout

    def test_rag_index_help(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["rag-index", "--help"])
        assert result.exit_code == 0
        assert "Index documents into RAG collection" in result.stdout

    def test_rag_index_nonexistent_source(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["rag-index", "/nonexistent/path"])
        assert result.exit_code == 1
        assert "does not exist" in result.stdout

    def test_rag_query_help(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["rag-query", "--help"])
        assert result.exit_code == 0
        assert "Query documents from RAG collection" in result.stdout

    def test_chat_command(self, runner: CliRunner) -> None:
        with (
            patch("testagent.agent.loop.agent_loop", new_callable=AsyncMock) as mock_loop,
            patch("testagent.llm.openai_provider.OpenAIProvider") as mock_provider,
        ):
            mock_loop.return_value = [{"role": "assistant", "content": "Mock response"}]
            mock_provider.return_value = MagicMock()
            result = runner.invoke(app, ["chat"], input="hello\nexit\n")
            assert result.exit_code == 0
            assert "TestAgent Chat" in result.stdout

    def test_chat_help_command(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["chat"], input="help\nexit\n")
        assert result.exit_code == 0
        assert "Commands:" in result.stdout

    def test_chat_clear_command(self, runner: CliRunner) -> None:
        with (
            patch("testagent.agent.loop.agent_loop", new_callable=AsyncMock) as mock_loop,
            patch("testagent.llm.openai_provider.OpenAIProvider") as mock_provider,
        ):
            mock_loop.return_value = [{"role": "assistant", "content": "Mock response"}]
            mock_provider.return_value = MagicMock()
            result = runner.invoke(app, ["chat"], input="test\nclear\nexit\n")
            assert result.exit_code == 0
            assert "Chat history cleared" in result.stdout


class TestRichOutput:
    """Test RichOutput formatting."""

    def test_print_header(self) -> None:
        output = RichOutput()
        with patch.object(output._console, "print") as mock_print:
            output.print_header(skill="api_smoke_test", target="https://example.com", timeout="60s")
            mock_print.assert_called_once()

    def test_print_task_result_passed(self) -> None:
        output = RichOutput()
        with patch.object(output._console, "print") as mock_print:
            task = {"name": "Test 1", "status": "passed", "duration": "1.2s"}
            output.print_task_result(1, 3, task)
            mock_print.assert_called_once()

    def test_print_task_result_failed(self) -> None:
        output = RichOutput()
        with patch.object(output._console, "print") as mock_print:
            task = {"name": "Test 2", "status": "failed", "duration": "0.5s"}
            output.print_task_result(2, 3, task)
            mock_print.assert_called_once()

    def test_print_task_result_flaky(self) -> None:
        output = RichOutput()
        with patch.object(output._console, "print") as mock_print:
            task = {"name": "Test 3", "status": "flaky", "duration": "2.0s"}
            output.print_task_result(3, 3, task)
            mock_print.assert_called_once()

    def test_print_task_result_defaults(self) -> None:
        output = RichOutput()
        with patch.object(output._console, "print") as mock_print:
            task: dict[str, object] = {}
            output.print_task_result(1, 1, task)
            mock_print.assert_called_once()

    def test_print_summary_all_passed(self) -> None:
        output = RichOutput()
        with patch.object(output._console, "print") as mock_print:
            output.print_summary(passed=5, failed=0, duration="10.5s")
            mock_print.assert_called_once()

    def test_print_summary_with_failures(self) -> None:
        output = RichOutput()
        with patch.object(output._console, "print") as mock_print:
            output.print_summary(passed=3, failed=2, duration="8.3s")
            mock_print.assert_called_once()

    def test_print_error(self) -> None:
        output = RichOutput()
        with patch.object(output._console, "print") as mock_print:
            output.print_error(task_id="task-001", error="Connection timeout")
            mock_print.assert_called_once()


class TestCliRegistration:
    """Test CLI sub-command registration."""

    def test_all_commands_in_help(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        for cmd_name in ("init", "run", "chat", "ci", "serve", "skill", "mcp", "rag-index", "rag-query"):
            assert cmd_name in result.stdout, f"Command '{cmd_name}' not found in help output"

    def test_skill_subcommands(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["skill", "list", "--help"])
        assert result.exit_code == 0
        result = runner.invoke(app, ["skill", "create", "--help"])
        assert result.exit_code == 0

    def test_mcp_subcommands(self, runner: CliRunner) -> None:
        result = runner.invoke(app, ["mcp", "list", "--help"])
        assert result.exit_code == 0
        result = runner.invoke(app, ["mcp", "add", "--help"])
        assert result.exit_code == 0
        result = runner.invoke(app, ["mcp", "health", "--help"])
        assert result.exit_code == 0
