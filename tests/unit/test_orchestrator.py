from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.harness.orchestrator import HarnessOrchestrator, OrchestratorError
from testagent.harness.runners.base import RunnerFactory, UnknownTaskTypeError
from testagent.harness.sandbox import ISandbox
from testagent.harness.sandbox_factory import IsolationLevel, SandboxFactory
from testagent.harness.snapshot import ExecutionSnapshot, SnapshotService
from testagent.models.plan import TestTask
from testagent.models.result import TestResult

# ====================================================================
# Helpers
# ====================================================================


def _make_task(
    *,
    task_id: str = "task-001",
    task_type: str = "api_test",
    isolation_level: str = "",
    status: str = "queued",
    task_config: dict[str, object] | None = None,
) -> TestTask:
    return TestTask(
        id=task_id,
        plan_id="plan-001",
        task_type=task_type,
        isolation_level=isolation_level,
        priority=1,
        status=status,
        retry_count=0,
        task_config=task_config or {"url": "http://example.com/api", "method": "GET"},
    )


def _make_sandbox_mock() -> MagicMock:
    sandbox = MagicMock(spec=ISandbox)
    sandbox.create = AsyncMock(return_value="sandbox-001")
    sandbox.execute = AsyncMock(return_value={"exit_code": 0, "stdout": "ok", "stderr": ""})
    sandbox.get_logs = AsyncMock(return_value="all logs")
    sandbox.get_artifacts = AsyncMock(return_value=[])
    sandbox.destroy = AsyncMock()
    return sandbox


def _make_runner_mock(result: TestResult | None = None) -> MagicMock:
    runner = MagicMock()
    runner.setup = AsyncMock()
    runner.execute = AsyncMock()
    runner.collect_results = AsyncMock(
        return_value=result
        or TestResult(
            task_id="task-001",
            status="passed",
            duration_ms=150.0,
            assertion_results={"status_code": {"passed": True}},
            logs="test passed",
        )
    )
    runner.teardown = AsyncMock()
    return runner


# ====================================================================
# TestHarnessOrchestrator
# ====================================================================


class TestDecideIsolation:
    """decide_isolation() — 用户显式指定 > 任务类型自动决策"""

    def test_user_explicit_docker(self) -> None:
        task = _make_task(isolation_level="docker")
        orchestrator = HarnessOrchestrator()
        level = orchestrator.decide_isolation(task)
        assert level == IsolationLevel.DOCKER

    def test_user_explicit_local(self) -> None:
        task = _make_task(isolation_level="local")
        orchestrator = HarnessOrchestrator()
        level = orchestrator.decide_isolation(task)
        assert level == IsolationLevel.LOCAL

    def test_user_explicit_microvm(self) -> None:
        task = _make_task(isolation_level="microvm")
        orchestrator = HarnessOrchestrator()
        level = orchestrator.decide_isolation(task)
        assert level == IsolationLevel.MICROVM

    def test_auto_decision_api_test(self) -> None:
        task = _make_task(task_type="api_test", isolation_level="")
        orchestrator = HarnessOrchestrator()
        level = orchestrator.decide_isolation(task)
        assert level == IsolationLevel.DOCKER

    def test_auto_decision_web_test(self) -> None:
        task = _make_task(task_type="web_test", isolation_level="")
        orchestrator = HarnessOrchestrator()
        level = orchestrator.decide_isolation(task)
        assert level == IsolationLevel.DOCKER

    def test_auto_decision_app_test(self) -> None:
        task = _make_task(task_type="app_test", isolation_level="")
        orchestrator = HarnessOrchestrator()
        level = orchestrator.decide_isolation(task)
        assert level == IsolationLevel.MICROVM

    def test_invalid_user_explicit_raises(self) -> None:
        task = _make_task(isolation_level="invalid_hypervisor")
        orchestrator = HarnessOrchestrator()
        with pytest.raises(OrchestratorError, match="Invalid isolation level") as exc_info:
            orchestrator.decide_isolation(task)
        assert exc_info.value.code == "INVALID_ISOLATION_LEVEL"
        assert exc_info.value.details["isolation_level"] == "invalid_hypervisor"


class TestDispatch:
    """dispatch() — 完整生命周期"""

    async def test_full_flow(self) -> None:
        task = _make_task()
        mock_sandbox = _make_sandbox_mock()
        expected_result = TestResult(
            task_id="task-001",
            status="passed",
            duration_ms=120.0,
        )
        mock_runner = _make_runner_mock(expected_result)

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
        assert result.duration_ms == 120.0

        mock_sandbox_factory.create.assert_called_once_with(IsolationLevel.DOCKER)
        mock_sandbox.create.assert_called_once_with(task.task_config)
        mock_runner_factory.get_runner.assert_called_once_with("api_test")
        mock_runner.setup.assert_called_once_with(
            task.task_config,
            sandbox=mock_sandbox,
            sandbox_id="sandbox-001",
        )
        mock_runner.execute.assert_called_once()
        mock_runner.collect_results.assert_called_once()
        mock_runner.teardown.assert_called_once()
        mock_sandbox.destroy.assert_called_once_with("sandbox-001")

    async def test_runner_teardown_and_sandbox_destroy_called_on_failure(self) -> None:
        task = _make_task()
        mock_sandbox = _make_sandbox_mock()
        mock_runner = _make_runner_mock()
        mock_runner.execute.side_effect = RuntimeError("Connection refused")

        mock_sandbox_factory = MagicMock(spec=SandboxFactory)
        mock_sandbox_factory.create.return_value = mock_sandbox
        mock_runner_factory = MagicMock(spec=RunnerFactory)
        mock_runner_factory.get_runner.return_value = mock_runner

        orchestrator = HarnessOrchestrator(
            sandbox_factory=mock_sandbox_factory,
            runner_factory=mock_runner_factory,
        )

        with pytest.raises(OrchestratorError, match="Dispatch failed"):
            await orchestrator.dispatch(task)

        mock_runner.teardown.assert_called_once()
        mock_sandbox.destroy.assert_called_once_with("sandbox-001")

    async def test_unknown_task_type(self) -> None:
        task = _make_task(task_type="unknown_type")
        mock_sandbox = _make_sandbox_mock()

        mock_sandbox_factory = MagicMock(spec=SandboxFactory)
        mock_sandbox_factory.create.return_value = mock_sandbox
        mock_runner_factory = MagicMock(spec=RunnerFactory)
        mock_runner_factory.get_runner.side_effect = UnknownTaskTypeError("unknown_type")

        orchestrator = HarnessOrchestrator(
            sandbox_factory=mock_sandbox_factory,
            runner_factory=mock_runner_factory,
        )

        with pytest.raises(OrchestratorError, match="Dispatch failed"):
            await orchestrator.dispatch(task)

        mock_runner_factory.get_runner.assert_called_once_with("unknown_type")
        mock_sandbox.destroy.assert_called_once_with("sandbox-001")


class TestDispatchWithRetry:
    """dispatch_with_retry() — 指数退避重试"""

    async def test_succeeds_on_first_attempt(self) -> None:
        task = _make_task()
        expected_result = TestResult(task_id="task-001", status="passed")

        mock_sandbox = _make_sandbox_mock()
        mock_runner = _make_runner_mock(expected_result)

        mock_sandbox_factory = MagicMock(spec=SandboxFactory)
        mock_sandbox_factory.create.return_value = mock_sandbox
        mock_runner_factory = MagicMock(spec=RunnerFactory)
        mock_runner_factory.get_runner.return_value = mock_runner

        orchestrator = HarnessOrchestrator(
            sandbox_factory=mock_sandbox_factory,
            runner_factory=mock_runner_factory,
        )

        result = await orchestrator.dispatch_with_retry(task)
        assert result.status == "passed"
        mock_runner.execute.assert_called_once()

    async def test_succeeds_on_second_attempt(self) -> None:
        task = _make_task()
        expected_result = TestResult(task_id="task-001", status="passed")

        mock_sandbox = _make_sandbox_mock()
        mock_runner = _make_runner_mock(expected_result)

        fail_count = 0

        async def _fail_then_succeed(*args: object, **kwargs: object) -> None:
            nonlocal fail_count
            fail_count += 1
            if fail_count == 1:
                raise RuntimeError("First attempt failed")

        mock_runner.execute.side_effect = _fail_then_succeed

        mock_sandbox_factory = MagicMock(spec=SandboxFactory)
        mock_sandbox_factory.create.return_value = mock_sandbox
        mock_runner_factory = MagicMock(spec=RunnerFactory)
        mock_runner_factory.get_runner.return_value = mock_runner

        orchestrator = HarnessOrchestrator(
            sandbox_factory=mock_sandbox_factory,
            runner_factory=mock_runner_factory,
        )

        with patch.object(asyncio, "sleep", AsyncMock()) as mock_sleep:
            result = await orchestrator.dispatch_with_retry(task)

        assert result.status == "passed"
        assert mock_runner.execute.call_count == 2
        mock_sleep.assert_awaited_once_with(2)

    async def test_all_three_attempts_fail(self) -> None:
        task = _make_task()
        mock_sandbox = _make_sandbox_mock()
        mock_runner = _make_runner_mock()
        mock_runner.execute.side_effect = RuntimeError("Persistent failure")

        mock_sandbox_factory = MagicMock(spec=SandboxFactory)
        mock_sandbox_factory.create.return_value = mock_sandbox
        mock_runner_factory = MagicMock(spec=RunnerFactory)
        mock_runner_factory.get_runner.return_value = mock_runner

        orchestrator = HarnessOrchestrator(
            sandbox_factory=mock_sandbox_factory,
            runner_factory=mock_runner_factory,
        )

        with (
            patch.object(asyncio, "sleep", AsyncMock()) as mock_sleep,
            pytest.raises(OrchestratorError, match="failed after 3 attempts") as exc_info,
        ):
            await orchestrator.dispatch_with_retry(task)

        assert exc_info.value.code == "MAX_RETRIES_EXCEEDED"
        assert mock_runner.execute.call_count == 3

        assert mock_sleep.await_count == 2
        mock_sleep.assert_any_call(2)
        mock_sleep.assert_any_call(4)

    async def test_exponential_backoff_timing(self) -> None:
        task = _make_task()
        mock_sandbox = _make_sandbox_mock()
        mock_runner = _make_runner_mock()
        mock_runner.execute.side_effect = RuntimeError("fail")

        mock_sandbox_factory = MagicMock(spec=SandboxFactory)
        mock_sandbox_factory.create.return_value = mock_sandbox
        mock_runner_factory = MagicMock(spec=RunnerFactory)
        mock_runner_factory.get_runner.return_value = mock_runner

        orchestrator = HarnessOrchestrator(
            sandbox_factory=mock_sandbox_factory,
            runner_factory=mock_runner_factory,
        )

        sleep_delays: list[float] = []

        async def _track_sleep(delay: float) -> None:
            sleep_delays.append(delay)

        with patch.object(asyncio, "sleep", _track_sleep), pytest.raises(OrchestratorError):
            await orchestrator.dispatch_with_retry(task)

        assert len(sleep_delays) == 2
        assert sleep_delays[0] == 2.0
        assert sleep_delays[1] == 4.0


# ====================================================================
# TestExecutionSnapshot
# ====================================================================


class TestExecutionSnapshot:
    """ExecutionSnapshot — 序列化/反序列化"""

    def test_to_dict_roundtrip(self) -> None:
        now = datetime.now(UTC)
        snap = ExecutionSnapshot(
            task_id="task-001",
            status="running",
            progress=0.3,
            checkpoint={"step": 2, "url": "http://example.com/page"},
            created_at=now,
        )

        data = snap.to_dict()
        restored = ExecutionSnapshot.from_dict(data)

        assert restored.task_id == snap.task_id
        assert restored.status == snap.status
        assert restored.progress == snap.progress
        assert restored.checkpoint == snap.checkpoint
        assert restored.created_at == snap.created_at

    def test_from_dict_default_created_at(self) -> None:
        data: dict[str, object] = {
            "task_id": "task-002",
            "status": "queued",
            "progress": 0.0,
            "checkpoint": {},
        }
        snap = ExecutionSnapshot.from_dict(data)
        assert snap.task_id == "task-002"
        assert snap.status == "queued"
        assert snap.progress == 0.0
        assert snap.checkpoint == {}

    def test_default_progress_computation(self) -> None:
        assert SnapshotService._compute_progress("queued") == 0.0
        assert SnapshotService._compute_progress("running") == 0.3
        assert SnapshotService._compute_progress("retrying") == 0.5
        assert SnapshotService._compute_progress("passed") == 1.0
        assert SnapshotService._compute_progress("unknown") == 0.0


# ====================================================================
# TestSnapshotService
# ====================================================================


class TestSnapshotService:
    """SnapshotService — 保存/加载/列出/恢复"""

    @pytest.fixture()
    def service(self, tmp_path: Path) -> SnapshotService:
        return SnapshotService(storage_dir=str(tmp_path))

    @pytest.fixture()
    def another_service(self, tmp_path: Path) -> SnapshotService:
        return SnapshotService(storage_dir=str(tmp_path))

    async def test_save_and_load(self, service: SnapshotService) -> None:
        await service.save(
            task_id="task-001",
            status="running",
            checkpoint={"step": 3, "url": "http://example.com"},
        )

        loaded = await service.load("task-001")
        assert loaded is not None
        assert loaded.task_id == "task-001"
        assert loaded.status == "running"
        assert loaded.progress == 0.3
        assert loaded.checkpoint == {"step": 3, "url": "http://example.com"}

    async def test_load_nonexistent(self, service: SnapshotService) -> None:
        loaded = await service.load("nonexistent-task")
        assert loaded is None

    async def test_list_incomplete(self, service: SnapshotService) -> None:
        running_id = "task-running"
        passed_id = "task-passed"
        failed_id = "task-failed"
        retrying_id = "task-retrying"

        await service.save(running_id, "running", {"progress": "middle"})
        await service.save(passed_id, "passed", {})
        await service.save(failed_id, "failed", {})
        await service.save(retrying_id, "retrying", {"attempt": 2})

        incomplete = await service.list_incomplete()
        task_ids = [s.task_id for s in incomplete]

        assert running_id in task_ids
        assert retrying_id in task_ids
        assert passed_id not in task_ids
        assert failed_id not in task_ids

    async def test_resume_existing(self, service: SnapshotService) -> None:
        await service.save(
            task_id="task-resume",
            status="running",
            checkpoint={"step": 5},
        )

        result = await service.resume("task-resume")
        assert result is not None
        assert result.task_id == "task-resume"
        assert result.status == "running"
        assert result.checkpoint == {"step": 5}

    async def test_resume_nonexistent(self, service: SnapshotService) -> None:
        result = await service.resume("no-such-task")
        assert result is None

    async def test_save_overwrites_existing(self, service: SnapshotService) -> None:
        await service.save("task-overwrite", "running", {"attempt": 1})
        await service.save("task-overwrite", "passed", {"final": True})

        loaded = await service.load("task-overwrite")
        assert loaded is not None
        assert loaded.status == "passed"
        assert loaded.checkpoint == {"final": True}

    async def test_list_incomplete_empty_directory(self, service: SnapshotService) -> None:
        incomplete = await service.list_incomplete()
        assert incomplete == []

    async def test_ignore_corrupted_files(self, service: SnapshotService) -> None:
        await service.save("task-good", "running", {})

        bad_path = Path(service._storage_dir) / "corrupt.json"
        bad_path.write_text("{invalid json", encoding="utf-8")

        incomplete = await service.list_incomplete()
        task_ids = [s.task_id for s in incomplete]
        assert "task-good" in task_ids
        assert len(incomplete) == 1

    async def test_custom_storage_dir(self, tmp_path: Path) -> None:
        custom_dir = tmp_path / "custom_snapshots"
        svc = SnapshotService(storage_dir=str(custom_dir))
        await svc.save("task-custom", "running", {"x": 1})
        loaded = await svc.load("task-custom")
        assert loaded is not None
        assert loaded.task_id == "task-custom"

    async def test_load_corrupted_file_returns_none(self, service: SnapshotService) -> None:
        bad_path = Path(service._storage_dir) / "bad.json"
        bad_path.parent.mkdir(parents=True, exist_ok=True)
        bad_path.write_text("{invalid}", encoding="utf-8")

        loaded = await service.load("bad")
        assert loaded is None
