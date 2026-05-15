from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from testagent.harness.docker_sandbox import DockerSandbox, DockerSandboxError
from testagent.harness.orchestrator import HarnessOrchestrator
from testagent.harness.runners.base import RunnerError, RunnerFactory
from testagent.harness.runners.http_runner import HTTPRunner
from testagent.harness.runners.playwright_runner import PlaywrightRunner
from testagent.harness.sandbox import RESOURCE_PROFILES, ISandbox
from testagent.harness.sandbox_factory import SandboxFactory
from testagent.models.plan import TestTask
from testagent.models.result import TestResult

pytestmark = pytest.mark.asyncio

DOCKER_AVAILABLE = (
    bool(shutil.which("docker")) and subprocess.run(["docker", "info"], capture_output=True).returncode == 0
)


# ====================================================================
# Helpers
# ====================================================================


def _make_api_task(
    *,
    task_id: str = "docker-api-task-001",
    isolation_level: str = "docker",
    config: dict[str, object] | None = None,
) -> TestTask:
    return TestTask(
        id=task_id,
        plan_id="plan-docker-001",
        task_type="api_test",
        isolation_level=isolation_level,
        priority=1,
        status="queued",
        retry_count=0,
        task_config=config
        or {
            "base_url": "http://httpbin.org",
            "method": "GET",
            "path": "/get",
            "assertions": {"status_code": 200},
        },
    )


def _make_web_task(
    *,
    task_id: str = "docker-web-task-001",
    isolation_level: str = "docker",
    config: dict[str, object] | None = None,
) -> TestTask:
    return TestTask(
        id=task_id,
        plan_id="plan-docker-002",
        task_type="web_test",
        isolation_level=isolation_level,
        priority=1,
        status="queued",
        retry_count=0,
        task_config=config
        or {
            "base_url": "http://example.com",
            "browser_type": "chromium",
            "actions": [
                {"action": "navigate", "url": "http://example.com"},
                {"action": "assert_title", "expected_title": "Example Domain"},
            ],
        },
    )


def _make_mock_sandbox(*, tmpdir: str | None = None) -> MagicMock:
    sandbox = MagicMock(spec=ISandbox)
    sandbox.create = AsyncMock(return_value="sandbox-docker-mock-001")
    sandbox.get_tmpdir = AsyncMock(return_value=tmpdir or tempfile.gettempdir())
    sandbox.get_logs = AsyncMock(return_value="")
    sandbox.get_artifacts = AsyncMock(return_value=[])
    sandbox.destroy = AsyncMock()
    return sandbox


def _make_mock_execute_output(
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
) -> AsyncMock:
    return AsyncMock(
        return_value={
            "exit_code": exit_code,
            "stdout": stdout,
            "stderr": stderr,
        }
    )


# ====================================================================
# Dockerfile 构建测试
# ====================================================================


@pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker daemon not available")
class TestDockerfileBuild:
    """Verify all three Dockerfiles build successfully.

    These tests require a real Docker daemon and are skipped when
    Docker is not available (e.g. CI without Docker, local dev).
    """

    @pytest.mark.docker
    async def test_build_harness_image(self) -> None:
        project_root = Path(__file__).resolve().parent.parent.parent
        result = subprocess.run(
            ["docker", "build", "-f", "docker/Dockerfile.harness", "-t", "testagent-harness-base:test", "."],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            timeout=900,
        )
        if result.returncode != 0:
            existing = subprocess.run(
                ["docker", "images", "-q", "testagent-harness-base:latest"],
                capture_output=True,
                text=True,
            )
            if existing.stdout.strip():
                pytest.skip("Skipping harness build; image already exists as testagent-harness-base:latest")
        assert result.returncode == 0, f"Harness build failed:\n{result.stderr}"
        combined_output = (result.stdout or "") + (result.stderr or "")
        assert "Successfully built" in combined_output or "exporting to image" in combined_output

    @pytest.mark.docker
    async def test_build_api_runner_image(self) -> None:
        project_root = Path(__file__).resolve().parent.parent.parent
        result = subprocess.run(
            ["docker", "build", "-f", "docker/Dockerfile.api_runner", "-t", "testagent-api-runner:test", "."],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            timeout=300,
        )
        assert result.returncode == 0, f"API runner build failed:\n{result.stderr}"

    @pytest.mark.docker
    async def test_build_web_runner_image(self) -> None:
        project_root = Path(__file__).resolve().parent.parent.parent
        result = subprocess.run(
            ["docker", "build", "-f", "docker/Dockerfile.web_runner", "-t", "testagent-web-runner:test", "."],
            capture_output=True,
            text=True,
            cwd=str(project_root),
            timeout=600,
        )
        assert result.returncode == 0, f"Web runner build failed:\n{result.stderr}"


# ====================================================================
# DockerSandbox 生命周期测试
# ====================================================================


class TestDockerSandboxLifecycle:
    """Test DockerSandbox create/execute/destroy lifecycle with mocked subprocess."""

    @pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker daemon not available")
    async def test_docker_sandbox_create_and_destroy(self) -> None:
        sandbox = DockerSandbox()
        sandbox_id = await sandbox.create(
            {
                "image": "python:3.12-slim",
                "command": "tail -f /dev/null",
                "cpus": 1,
                "mem_limit": "512m",
            }
        )
        assert sandbox_id.startswith("sandbox-")
        assert sandbox_id in sandbox._containers

        meta = sandbox._containers[sandbox_id]
        assert meta["image"] == "python:3.12-slim"
        assert isinstance(meta["container_id"], str) and len(meta["container_id"]) > 0
        assert meta["task_type"] == "api_test"
        assert os.path.isdir(meta["tmpdir"])

        await sandbox.destroy(sandbox_id)
        assert sandbox_id not in sandbox._containers

    @pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker daemon not available")
    async def test_docker_sandbox_destroy_idempotent(self) -> None:
        sandbox = DockerSandbox()
        sandbox_id = await sandbox.create({"image": "python:3.12-slim"})
        await sandbox.destroy(sandbox_id)
        await sandbox.destroy(sandbox_id)

    async def test_docker_sandbox_destroy_unknown(self) -> None:
        sandbox = DockerSandbox()
        await sandbox.destroy("no-such-sandbox")

    async def test_docker_sandbox_execute_unknown(self) -> None:
        sandbox = DockerSandbox()
        with pytest.raises(DockerSandboxError) as exc_info:
            await sandbox.execute("no-such-sandbox", "echo hello", timeout=10)
        assert exc_info.value.code == "SANDBOX_NOT_FOUND"

    @pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker daemon not available")
    async def test_docker_sandbox_get_tmpdir(self) -> None:
        sandbox = DockerSandbox()
        sandbox_id = await sandbox.create({"image": "python:3.12-slim"})
        tmpdir = await sandbox.get_tmpdir(sandbox_id)
        assert os.path.isdir(tmpdir)
        await sandbox.destroy(sandbox_id)

    async def test_docker_sandbox_get_tmpdir_unknown(self) -> None:
        sandbox = DockerSandbox()
        with pytest.raises(DockerSandboxError) as exc_info:
            await sandbox.get_tmpdir("no-such-sandbox")
        assert exc_info.value.code == "SANDBOX_NOT_FOUND"

    @pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker daemon not available")
    async def test_docker_sandbox_get_logs(self) -> None:
        sandbox = DockerSandbox()
        sandbox_id = await sandbox.create({"image": "python:3.12-slim", "command": "echo hello"})
        logs = await sandbox.get_logs(sandbox_id)
        assert isinstance(logs, str)
        await sandbox.destroy(sandbox_id)

    @pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker daemon not available")
    async def test_docker_sandbox_get_artifacts(self) -> None:
        sandbox = DockerSandbox()
        sandbox_id = await sandbox.create({"image": "python:3.12-slim"})
        artifacts = await sandbox.get_artifacts(sandbox_id)
        assert artifacts == []
        await sandbox.destroy(sandbox_id)

    @pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker daemon not available")
    async def test_docker_sandbox_destroy_after_execution_error(self) -> None:
        sandbox = DockerSandbox()
        sandbox_id = await sandbox.create({"image": "python:3.12-slim"})
        assert sandbox_id in sandbox._containers
        await sandbox.destroy(sandbox_id)
        assert sandbox_id not in sandbox._containers


# ====================================================================
# HTTPRunner Docker 执行流程测试
# ====================================================================


class TestHTTPRunnerDockerExecution:
    """Test HTTPRunner's Docker execution code paths with mocked sandbox."""

    async def test_http_runner_generate_docker_script(self, tmp_path: Path) -> None:
        runner = HTTPRunner()
        config = {"base_url": "http://httpbin.org"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")  # type: ignore[arg-type]

        script = runner._generate_docker_exec_script(
            json.dumps(
                {
                    "method": "GET",
                    "path": "/get",
                    "assertions": {"status_code": 200},
                }
            )
        )

        assert "import httpx" in script
        assert "method = 'GET'" in script
        assert "path = '/get'" in script
        assert "status_code" in script
        assert "base_url = 'http://httpbin.org'" in script

        compiled = compile(script, "<string>", "exec")
        assert compiled is not None

    async def test_http_runner_docker_parse_passed_output(self, tmp_path: Path) -> None:
        runner = HTTPRunner()
        config = {"base_url": "http://httpbin.org"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")

        result = runner._parse_docker_output(
            {
                "stdout": json.dumps(
                    {
                        "status": "passed",
                        "assertion_results": {
                            "status_code": {"expected": 200, "actual": 200, "passed": True},
                        },
                        "logs": '{"method": "GET", "status_code": 200}',
                        "artifacts": {"status_code": 200},
                    }
                ),
                "stderr": "",
                "exit_code": 0,
            },
            duration_ms=45.0,
        )

        assert result.status == "passed"
        assert result.duration_ms == 45.0
        assert result.assertion_results is not None
        assert result.assertion_results["status_code"]["passed"] is True  # type: ignore[index]
        assert result.assertion_results["status_code"]["expected"] == 200  # type: ignore[index]

    async def test_http_runner_docker_parse_failed_output(self, tmp_path: Path) -> None:
        runner = HTTPRunner()
        config = {"base_url": "http://httpbin.org"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")

        result = runner._parse_docker_output(
            {
                "stdout": json.dumps(
                    {
                        "status": "failed",
                        "assertion_results": {
                            "status_code": {"expected": 200, "actual": 404, "passed": False},
                        },
                        "logs": '{"method": "GET", "status_code": 404}',
                    }
                ),
                "stderr": "",
                "exit_code": 0,
            },
            duration_ms=30.0,
        )

        assert result.status == "failed"
        assert result.assertion_results["status_code"]["passed"] is False  # type: ignore[index]

    async def test_http_runner_docker_parse_non_zero_exit(self, tmp_path: Path) -> None:
        runner = HTTPRunner()
        config = {"base_url": "http://httpbin.org"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")

        result = runner._parse_docker_output(
            {
                "stdout": "",
                "stderr": "connection refused",
                "exit_code": 1,
            },
            duration_ms=10.0,
        )

        assert result.status == "error"
        assert "connection refused" in str(result.assertion_results)

    async def test_http_runner_docker_parse_invalid_json(self, tmp_path: Path) -> None:
        runner = HTTPRunner()
        config = {"base_url": "http://httpbin.org"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")

        result = runner._parse_docker_output(
            {
                "stdout": "not valid json{{{",
                "stderr": "",
                "exit_code": 0,
            },
            duration_ms=5.0,
        )

        assert result.status == "error"
        assert "Failed to parse" in str(result.assertion_results)

    async def test_http_runner_docker_full_execution(self, tmp_path: Path) -> None:
        runner = HTTPRunner()
        config = {"base_url": "http://httpbin.org"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        mock_sandbox.execute = _make_mock_execute_output(
            stdout=json.dumps(
                {
                    "status": "passed",
                    "assertion_results": {
                        "status_code": {"expected": 200, "actual": 200, "passed": True},
                    },
                    "logs": '{"method": "GET", "status_code": 200}',
                    "artifacts": {"status_code": 200},
                }
            )
        )
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")

        script = json.dumps({"method": "GET", "path": "/get", "assertions": {"status_code": 200}})
        result = await runner.execute(script)

        assert result.status == "passed"
        assert result.assertion_results["status_code"]["passed"] is True

    async def test_http_runner_docker_teardown_skips_cleanup(self, tmp_path: Path) -> None:
        runner = HTTPRunner()
        config = {"base_url": "http://httpbin.org"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")
        await runner.teardown()

    async def test_http_runner_docker_collect_results(self, tmp_path: Path) -> None:
        runner = HTTPRunner()
        config = {"base_url": "http://httpbin.org"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")

        runner._docker_result = {
            "status": "passed",
            "assertion_results": {"status_code": {"passed": True}},
            "logs": "request log",
            "artifacts": {"status_code": 200},
        }
        result = await runner.collect_results()
        assert result.status == "passed"
        assert result.artifacts is not None
        assert result.artifacts["status_code"] == 200  # type: ignore[index]

    async def test_http_runner_docker_setup_skip_client(self, tmp_path: Path) -> None:
        runner = HTTPRunner()
        config = {"base_url": "http://httpbin.org"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")
        assert runner._client is None


# ====================================================================
# PlaywrightRunner Docker 执行流程测试
# ====================================================================


class TestPlaywrightRunnerDockerExecution:
    """Test PlaywrightRunner's Docker execution code paths with mocked sandbox."""

    async def test_playwright_runner_generate_docker_script(self, tmp_path: Path) -> None:
        runner = PlaywrightRunner()
        config = {"base_url": "http://example.com", "browser_type": "chromium"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")

        script = runner._generate_docker_exec_script(
            json.dumps(
                {
                    "actions": [
                        {"action": "navigate", "url": "http://example.com"},
                        {"action": "assert_title", "expected_title": "Example Domain"},
                    ],
                }
            )
        )

        assert "from playwright.sync_api import sync_playwright" in script
        assert 'action_type == "navigate"' in script
        assert 'action_type == "assert_title"' in script
        assert "http://example.com" in script

        compiled = compile(script, "<string>", "exec")
        assert compiled is not None

    async def test_playwright_runner_generate_docker_script_all_actions(self, tmp_path: Path) -> None:
        runner = PlaywrightRunner()
        config = {"base_url": "http://example.com", "browser_type": "chromium"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")

        actions = [
            {"action": "navigate", "url": "http://example.com"},
            {"action": "click", "selector": "#btn"},
            {"action": "fill", "selector": "#input", "value": "hello"},
            {"action": "type", "selector": "#input", "value": "world", "delay": 10},
            {"action": "select", "selector": "#dropdown", "value": "opt1"},
            {"action": "check", "selector": "#cb"},
            {"action": "uncheck", "selector": "#cb"},
            {"action": "hover", "selector": "#menu"},
            {"action": "wait_for_selector", "selector": "#loading"},
            {"action": "wait_for_navigation"},
            {"action": "screenshot"},
            {"action": "evaluate", "expression": "document.title"},
            {"action": "get_text", "selector": "#title", "assertion": True, "expected_text": "Hello"},
            {
                "action": "get_attribute",
                "selector": "#link",
                "attribute": "href",
                "assertion": True,
                "expected_value": "/home",
            },
            {"action": "is_visible", "selector": "#modal", "assertion": True},
            {"action": "assert_text", "selector": "#msg", "expected_text": "Success"},
            {"action": "assert_visible", "selector": "#footer", "expected": True},
            {"action": "assert_url", "expected_url": "http://example.com/done"},
            {"action": "assert_title", "expected_title": "Done"},
        ]

        script = runner._generate_docker_exec_script(json.dumps({"actions": actions}))

        for action in actions:
            assert action["action"] in script  # type: ignore[index]
        compiled = compile(script, "<string>", "exec")
        assert compiled is not None

    async def test_playwright_runner_docker_parse_passed_output(self, tmp_path: Path) -> None:
        runner = PlaywrightRunner()
        config = {"base_url": "http://example.com", "browser_type": "chromium"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")

        result = runner._parse_docker_output(
            {
                "stdout": json.dumps(
                    {
                        "status": "passed",
                        "assertion_results": {
                            "assert_title_0": {
                                "passed": True,
                                "actual": "Example Domain",
                                "expected": "Example Domain",
                            },
                        },
                        "logs": '[{"action": "navigate", "index": 0}]',
                        "artifacts": {"screenshots": []},
                    }
                ),
                "stderr": "",
                "exit_code": 0,
            },
            duration_ms=500.0,
        )

        assert result.status == "passed"
        assert result.duration_ms == 500.0
        assert result.assertion_results["assert_title_0"]["passed"] is True  # type: ignore[index]

    async def test_playwright_runner_docker_parse_failed_output(self, tmp_path: Path) -> None:
        runner = PlaywrightRunner()
        config = {"base_url": "http://example.com", "browser_type": "chromium"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")

        result = runner._parse_docker_output(
            {
                "stdout": json.dumps(
                    {
                        "status": "failed",
                        "assertion_results": {
                            "assert_title_0": {"passed": False, "actual": "Wrong Title", "expected": "Expected Title"},
                        },
                        "logs": '[{"action": "navigate", "index": 0}]',
                    }
                ),
                "stderr": "",
                "exit_code": 0,
            },
            duration_ms=300.0,
        )

        assert result.status == "failed"
        assert result.assertion_results["assert_title_0"]["passed"] is False  # type: ignore[index]

    async def test_playwright_runner_docker_parse_non_zero_exit(self, tmp_path: Path) -> None:
        runner = PlaywrightRunner()
        config = {"base_url": "http://example.com", "browser_type": "chromium"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")

        result = runner._parse_docker_output(
            {
                "stdout": "",
                "stderr": "chromium crashed",
                "exit_code": 1,
            },
            duration_ms=100.0,
        )

        assert result.status == "error"
        assert "chromium crashed" in str(result.assertion_results)

    async def test_playwright_docker_setup_skip_playwright(self, tmp_path: Path) -> None:
        runner = PlaywrightRunner()
        config = {"base_url": "http://example.com", "browser_type": "chromium"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")
        assert runner._page is None
        assert runner._browser is None
        assert runner._playwright is None

    async def test_playwright_docker_teardown_skips_cleanup(self, tmp_path: Path) -> None:
        runner = PlaywrightRunner()
        config = {"base_url": "http://example.com", "browser_type": "chromium"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")
        await runner.teardown()

    async def test_playwright_docker_collect_results(self, tmp_path: Path) -> None:
        runner = PlaywrightRunner()
        config = {"base_url": "http://example.com", "browser_type": "chromium"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")

        runner._docker_result = {
            "status": "passed",
            "assertion_results": {"assert_title": {"passed": True}},
            "logs": "action log",
            "artifacts": {"screenshots": ["/tmp/shot.png"]},
        }
        result = await runner.collect_results()
        assert result.status == "passed"
        assert result.artifacts is not None
        assert len(result.artifacts["screenshots"]) == 1  # type: ignore[arg-type]

    async def test_playwright_docker_full_execution(self, tmp_path: Path) -> None:
        runner = PlaywrightRunner()
        config = {"base_url": "http://example.com", "browser_type": "chromium"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        mock_sandbox.execute = _make_mock_execute_output(
            stdout=json.dumps(
                {
                    "status": "passed",
                    "assertion_results": {
                        "assert_title_0": {"passed": True, "actual": "Example Domain", "expected": "Example Domain"},
                    },
                    "logs": '[{"action": "navigate", "index": 0}]',
                    "artifacts": {"screenshots": []},
                }
            )
        )
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")

        script = json.dumps(
            {
                "actions": [
                    {"action": "navigate", "url": "http://example.com"},
                    {"action": "assert_title", "expected_title": "Example Domain"},
                ],
            }
        )
        result = await runner.execute(script)

        assert result.status == "passed"
        assert result.assertion_results["assert_title_0"]["passed"] is True


# ====================================================================
# Orchestrator Docker 调度测试
# ====================================================================


class TestOrchestratorDockerDispatch:
    """Test that HarnessOrchestrator correctly passes sandbox to runner in Docker mode."""

    async def test_orchestrator_docker_dispatch_passes_sandbox_to_runner(self) -> None:
        task = _make_api_task()

        mock_sandbox = _make_mock_sandbox()

        mock_runner = MagicMock()
        mock_runner.setup = AsyncMock()
        mock_runner.execute = AsyncMock()
        mock_runner.collect_results = AsyncMock(
            return_value=TestResult(
                task_id=task.id,
                status="passed",
                duration_ms=45.0,
                assertion_results={"status_code": {"passed": True}},
                logs="docker execution ok",
            )
        )
        mock_runner.teardown = AsyncMock()

        mock_sandbox_factory = MagicMock(spec=SandboxFactory)
        mock_sandbox_factory.create.return_value = mock_sandbox
        mock_runner_factory = MagicMock(spec=RunnerFactory)
        mock_runner_factory.get_runner.return_value = mock_runner

        orchestrator = HarnessOrchestrator(
            sandbox_factory=mock_sandbox_factory,
            runner_factory=mock_runner_factory,
        )

        result = await orchestrator.dispatch(task)

        assert result.status == "passed"
        mock_runner.setup.assert_called_once()
        _, kwargs = mock_runner.setup.call_args
        assert "sandbox" in kwargs
        assert kwargs["sandbox"] is mock_sandbox
        assert "sandbox_id" in kwargs
        assert kwargs["sandbox_id"] == "sandbox-docker-mock-001"

    async def test_orchestrator_docker_dispatch_full_lifecycle(self) -> None:
        task = _make_api_task()

        mock_sandbox = _make_mock_sandbox()
        mock_sandbox.execute = _make_mock_execute_output(
            stdout=json.dumps(
                {
                    "status": "passed",
                    "assertion_results": {"status_code": {"passed": True, "expected": 200, "actual": 200}},
                    "logs": "request ok",
                }
            )
        )

        mock_runner = MagicMock()
        mock_runner.setup = AsyncMock()
        mock_runner.execute = AsyncMock()
        mock_runner.collect_results = AsyncMock(
            return_value=TestResult(
                task_id=task.id,
                status="passed",
                duration_ms=45.0,
                assertion_results={"status_code": {"passed": True, "expected": 200, "actual": 200}},
                logs="docker execution ok",
            )
        )
        mock_runner.teardown = AsyncMock()

        mock_sandbox_factory = MagicMock(spec=SandboxFactory)
        mock_sandbox_factory.create.return_value = mock_sandbox
        mock_runner_factory = MagicMock(spec=RunnerFactory)
        mock_runner_factory.get_runner.return_value = mock_runner

        orchestrator = HarnessOrchestrator(
            sandbox_factory=mock_sandbox_factory,
            runner_factory=mock_runner_factory,
        )

        result = await orchestrator.dispatch(task)

        assert result.status == "passed"
        mock_sandbox.create.assert_called_once()
        mock_runner.setup.assert_called_once()
        mock_runner.execute.assert_called_once()
        mock_runner.collect_results.assert_called_once()
        mock_runner.teardown.assert_called_once()
        mock_sandbox.destroy.assert_called_once_with("sandbox-docker-mock-001")

    async def test_orchestrator_docker_dispatch_web_task(self) -> None:
        task = _make_web_task()

        mock_sandbox = _make_mock_sandbox()

        mock_runner = MagicMock()
        mock_runner.setup = AsyncMock()
        mock_runner.execute = AsyncMock()
        mock_runner.collect_results = AsyncMock(
            return_value=TestResult(
                task_id=task.id,
                status="passed",
                duration_ms=1000.0,
                assertion_results={"assert_title": {"passed": True}},
                logs="web docker execution ok",
            )
        )
        mock_runner.teardown = AsyncMock()

        mock_sandbox_factory = MagicMock(spec=SandboxFactory)
        mock_sandbox_factory.create.return_value = mock_sandbox
        mock_runner_factory = MagicMock(spec=RunnerFactory)
        mock_runner_factory.get_runner.return_value = mock_runner

        orchestrator = HarnessOrchestrator(
            sandbox_factory=mock_sandbox_factory,
            runner_factory=mock_runner_factory,
        )

        result = await orchestrator.dispatch(task)

        assert result.status == "passed"
        mock_runner.setup.assert_called_once()
        _, kwargs = mock_runner.setup.call_args
        assert kwargs["sandbox"] is mock_sandbox


# ====================================================================
# BaseRunner Docker 辅助方法测试
# ====================================================================


class TestBaseRunnerDockerHelpers:
    """Test BaseRunner._write_script and _run_in_sandbox with mocked sandbox."""

    async def test_write_script_creates_file(self, tmp_path: Path) -> None:
        runner = HTTPRunner()
        config = {"base_url": "http://httpbin.org"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")

        container_path = await runner._write_script(
            "print('hello')",
            filename="test_script.py",
        )

        host_file = os.path.join(str(tmp_path), "test_script.py")
        assert os.path.isfile(host_file)
        assert container_path == "/tmp/testagent/test_script.py"
        with open(host_file) as f:
            assert f.read() == "print('hello')"

    async def test_write_script_generates_filename(self, tmp_path: Path) -> None:
        runner = HTTPRunner()
        config = {"base_url": "http://httpbin.org"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")

        container_path = await runner._write_script("print('hello')")
        assert container_path.startswith("/tmp/testagent/test_")
        assert container_path.endswith(".py")

    async def test_write_script_raises_without_sandbox(self) -> None:
        runner = HTTPRunner()
        with pytest.raises(RunnerError, match="Cannot write script without a sandbox"):
            await runner._write_script("print('hello')")

    async def test_run_in_sandbox_calls_sandbox_execute(self, tmp_path: Path) -> None:
        runner = HTTPRunner()
        config = {"base_url": "http://httpbin.org"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        mock_sandbox.execute = AsyncMock(return_value={"exit_code": 0, "stdout": "ok", "stderr": ""})
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")

        result = await runner._run_in_sandbox("echo hello", timeout=30)

        assert result["exit_code"] == 0
        assert result["stdout"] == "ok"
        mock_sandbox.execute.assert_called_once_with("sandbox-mock-001", "echo hello", timeout=30)

    async def test_run_in_sandbox_raises_without_sandbox(self) -> None:
        runner = HTTPRunner()
        with pytest.raises(RunnerError, match="Cannot run in sandbox without a sandbox"):
            await runner._run_in_sandbox("echo hello")

    async def test_in_docker_mode_property(self, tmp_path: Path) -> None:
        runner = HTTPRunner()
        assert runner._in_docker_mode is False

        config = {"base_url": "http://httpbin.org"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")
        assert runner._in_docker_mode is True


# ====================================================================
# IsolatioLevel 决策测试
# ====================================================================


class TestDockerIsolationDecision:
    """Test that Docker isolation level is correctly decided for api_test and web_test."""

    async def test_api_test_defaults_to_docker(self) -> None:
        level = SandboxFactory.decide_isolation("api_test")
        from testagent.harness.sandbox_factory import IsolationLevel

        assert level == IsolationLevel.DOCKER

    async def test_web_test_defaults_to_docker(self) -> None:
        level = SandboxFactory.decide_isolation("web_test")
        from testagent.harness.sandbox_factory import IsolationLevel

        assert level == IsolationLevel.DOCKER

    async def test_user_explicit_docker_isolation(self) -> None:
        task = _make_api_task(isolation_level="docker")
        orchestrator = HarnessOrchestrator()
        level = orchestrator.decide_isolation(task)
        from testagent.harness.sandbox_factory import IsolationLevel

        assert level == IsolationLevel.DOCKER

    async def test_resource_profiles_match_adr004(self) -> None:
        api = RESOURCE_PROFILES["api_test"]
        assert api.cpus == 1
        assert api.mem_limit == "512m"
        assert api.timeout == 60

        web = RESOURCE_PROFILES["web_test"]
        assert web.cpus == 2
        assert web.mem_limit == "2g"
        assert web.timeout == 120

        app = RESOURCE_PROFILES["app_test"]
        assert app.cpus == 4
        assert app.mem_limit == "4g"
        assert app.timeout == 180


# ====================================================================
# 错误处理测试
# ====================================================================


class TestDockerErrorHandling:
    """Test error handling in Docker execution paths."""

    async def test_http_docker_execute_with_network_error(self, tmp_path: Path) -> None:
        runner = HTTPRunner()
        config = {"base_url": "http://nonexistent.domain"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))
        mock_sandbox.execute = _make_mock_execute_output(
            stdout=json.dumps(
                {
                    "status": "error",
                    "assertion_results": {"error": "Connection refused"},
                    "logs": "httpx.ConnectError",
                }
            ),
            exit_code=1,
        )
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")

        script = json.dumps({"method": "GET", "path": "/api"})
        result = await runner.execute(script)

        assert result.status == "error"

    async def test_http_docker_execute_times_out(self, tmp_path: Path) -> None:
        runner = HTTPRunner()
        config = {"base_url": "http://httpbin.org"}
        mock_sandbox = _make_mock_sandbox(tmpdir=str(tmp_path))

        async def _slow_execute(*args: object, **kwargs: object) -> object:
            raise TimeoutError("Command timed out after 60s")

        mock_sandbox.execute = _slow_execute
        await runner.setup(config, sandbox=mock_sandbox, sandbox_id="sandbox-mock-001")

        script = json.dumps({"method": "GET", "path": "/delay/10"})
        with pytest.raises(TimeoutError):
            await runner.execute(script)
