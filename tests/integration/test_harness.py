from __future__ import annotations

import asyncio
import shutil
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from testagent.common.errors import SandboxTimeoutError
from testagent.harness.orchestrator import HarnessOrchestrator, OrchestratorError
from testagent.harness.runners.base import RunnerFactory
from testagent.harness.sandbox import RESOURCE_PROFILES, ISandbox
from testagent.harness.sandbox_factory import IsolationLevel, SandboxFactory
from testagent.harness.snapshot import ExecutionSnapshot, SnapshotService
from testagent.models.plan import TestTask
from testagent.models.result import TestResult

# ====================================================================
# Helpers
# ====================================================================


def _make_api_task(
    *,
    task_id: str = "integ-task-001",
    isolation_level: str = "local",
    status: str = "queued",
    retry_count: int = 0,
    config: dict[str, object] | None = None,
) -> TestTask:
    return TestTask(
        id=task_id,
        plan_id="plan-integ-001",
        task_type="api_test",
        isolation_level=isolation_level,
        priority=1,
        status=status,
        retry_count=retry_count,
        task_config=config
        or {
            "base_url": "http://localhost:9999",
            "method": "GET",
            "path": "/api/health",
            "assertions": {"status_code": 200},
        },
    )


@pytest.fixture()
def _allow_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTAGENT_ALLOW_LOCAL", "1")


@pytest.fixture()
def _mock_execute_passed() -> TestResult:
    return TestResult(
        task_id="integ-task-001",
        status="passed",
        duration_ms=45.0,
        assertion_results={
            "status_code": {"expected": 200, "actual": 200, "passed": True},
        },
        logs='{"method": "GET", "path": "/api/health", "status_code": 200, "duration_ms": 45.0}',
    )


@pytest.fixture()
def _mock_execute_failed() -> TestResult:
    return TestResult(
        task_id="integ-task-001",
        status="failed",
        duration_ms=30.0,
        assertion_results={
            "status_code": {"expected": 200, "actual": 404, "passed": False},
        },
        logs='{"method": "GET", "path": "/api/not-found", "status_code": 404, "duration_ms": 30.0}',
    )


# ====================================================================
# test_harness_api_test_dispatch
# ====================================================================


@pytest.mark.asyncio
async def test_harness_api_test_dispatch(
    _allow_local: None,
    _mock_execute_passed: TestResult,
) -> None:
    """Integration test for HarnessOrchestrator.dispatch().

    1. Create an API test task with local isolation
    2. Call HarnessOrchestrator.dispatch()
    3. Verify the full sandbox lifecycle: create -> runner setup -> execute -> collect -> teardown -> destroy
    4. Verify result collection with assertion details
    """
    task = _make_api_task()

    orchestrator = HarnessOrchestrator()

    mock_runner = MagicMock()
    mock_runner.setup = AsyncMock()
    mock_runner.execute = AsyncMock()
    mock_runner.collect_results = AsyncMock(return_value=_mock_execute_passed)
    mock_runner.teardown = AsyncMock()

    mock_sandbox = MagicMock(spec=ISandbox)
    mock_sandbox.create = AsyncMock(return_value="sandbox-dispatch-001")
    mock_sandbox.execute = AsyncMock()
    mock_sandbox.get_logs = AsyncMock(return_value="")
    mock_sandbox.get_artifacts = AsyncMock(return_value=[])
    mock_sandbox.destroy = AsyncMock()

    mock_sandbox_factory = MagicMock(spec=SandboxFactory)
    mock_sandbox_factory.create.return_value = mock_sandbox
    mock_runner_factory = MagicMock(spec=RunnerFactory)
    mock_runner_factory.get_runner.return_value = mock_runner

    orchestrator = HarnessOrchestrator(
        sandbox_factory=mock_sandbox_factory,
        runner_factory=mock_runner_factory,
    )

    result = await orchestrator.dispatch(task)

    assert result is not None
    assert result.status == "passed"
    assert result.task_id == "integ-task-001"
    assert result.duration_ms is not None
    assert result.duration_ms > 0

    assert result.assertion_results is not None
    ar: Any = result.assertion_results
    assert "status_code" in ar
    assert ar["status_code"]["passed"] is True
    assert ar["status_code"]["expected"] == 200
    assert ar["status_code"]["actual"] == 200

    assert result.logs is not None
    assert "GET" in result.logs
    assert "/api/health" in result.logs

    mock_runner.setup.assert_called_once()
    mock_runner.execute.assert_called_once()
    mock_runner.collect_results.assert_called_once()
    mock_runner.teardown.assert_called_once()
    mock_sandbox.destroy.assert_called_once_with("sandbox-dispatch-001")


@pytest.mark.asyncio
async def test_harness_api_test_dispatch_failed_assertion(
    _allow_local: None,
    _mock_execute_failed: TestResult,
) -> None:
    """Verify dispatch captures a failed assertion result correctly."""
    task = _make_api_task(
        config={
            "base_url": "http://localhost:9999",
            "method": "GET",
            "path": "/api/not-found",
            "assertions": {"status_code": 200},
        }
    )

    mock_runner = MagicMock()
    mock_runner.setup = AsyncMock()
    mock_runner.execute = AsyncMock()
    mock_runner.collect_results = AsyncMock(return_value=_mock_execute_failed)
    mock_runner.teardown = AsyncMock()

    mock_sandbox = MagicMock(spec=ISandbox)
    mock_sandbox.create = AsyncMock(return_value="sandbox-dispatch-002")
    mock_sandbox.execute = AsyncMock()
    mock_sandbox.get_logs = AsyncMock(return_value="")
    mock_sandbox.get_artifacts = AsyncMock(return_value=[])
    mock_sandbox.destroy = AsyncMock()

    mock_sandbox_factory = MagicMock(spec=SandboxFactory)
    mock_sandbox_factory.create.return_value = mock_sandbox
    mock_runner_factory = MagicMock(spec=RunnerFactory)
    mock_runner_factory.get_runner.return_value = mock_runner

    orchestrator = HarnessOrchestrator(
        sandbox_factory=mock_sandbox_factory,
        runner_factory=mock_runner_factory,
    )

    result = await orchestrator.dispatch(task)

    assert result.status == "failed"
    assert result.assertion_results is not None
    ar: Any = result.assertion_results
    assert ar["status_code"]["passed"] is False
    assert ar["status_code"]["expected"] == 200
    assert ar["status_code"]["actual"] == 404


# ====================================================================
# test_harness_retry_on_failure
# ====================================================================


@pytest.mark.asyncio
async def test_harness_retry_on_failure_succeeds_on_first_attempt(
    _allow_local: None,
) -> None:
    """dispatch_with_retry succeeds on first attempt -> no retries."""
    task = _make_api_task()

    mock_runner = MagicMock()
    mock_runner.setup = AsyncMock()
    mock_runner.execute = AsyncMock()
    mock_runner.collect_results = AsyncMock(
        return_value=TestResult(
            task_id="integ-task-001",
            status="passed",
            duration_ms=45.0,
            assertion_results={"status_code": {"passed": True}},
            logs="first attempt ok",
        )
    )
    mock_runner.teardown = AsyncMock()

    mock_sandbox = MagicMock(spec=ISandbox)
    mock_sandbox.create = AsyncMock(return_value="sandbox-retry-001")
    mock_sandbox.execute = AsyncMock()
    mock_sandbox.get_logs = AsyncMock(return_value="")
    mock_sandbox.get_artifacts = AsyncMock(return_value=[])
    mock_sandbox.destroy = AsyncMock()

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
    mock_runner.setup.assert_called_once_with(
        task.task_config,
        sandbox=mock_sandbox,
        sandbox_id="sandbox-retry-001",
    )
    mock_runner.collect_results.assert_called_once()
    mock_runner.teardown.assert_called_once()
    mock_sandbox.destroy.assert_called_once_with("sandbox-retry-001")


@pytest.mark.asyncio
async def test_harness_retry_on_failure_exponential_backoff(
    _allow_local: None,
) -> None:
    """Verify 2 retries with 2s -> 4s exponential backoff when first attempts fail.

    1. Runner.execute raises on first two attempts
    2. Third attempt succeeds
    3. Verify sleep delays: 2.0, 4.0
    4. Verify exactly 3 execute calls
    """
    task = _make_api_task()

    mock_runner = MagicMock()
    mock_runner.setup = AsyncMock()
    mock_runner.collect_results = AsyncMock(
        return_value=TestResult(
            task_id="integ-task-001",
            status="passed",
            duration_ms=60.0,
            assertion_results={"status_code": {"passed": True}},
            logs="final attempt ok",
        )
    )
    mock_runner.teardown = AsyncMock()

    fail_count = 0

    async def _fail_then_succeed(*args: object, **kwargs: object) -> None:
        nonlocal fail_count
        fail_count += 1
        if fail_count <= 2:
            raise RuntimeError(f"Attempt {fail_count} failed")

    mock_runner.execute.side_effect = _fail_then_succeed

    mock_sandbox = MagicMock(spec=ISandbox)
    mock_sandbox.create = AsyncMock(return_value="sandbox-retry-002")
    mock_sandbox.execute = AsyncMock()
    mock_sandbox.get_logs = AsyncMock(return_value="")
    mock_sandbox.get_artifacts = AsyncMock(return_value=[])
    mock_sandbox.destroy = AsyncMock()

    mock_sandbox_factory = MagicMock(spec=SandboxFactory)
    mock_sandbox_factory.create.return_value = mock_sandbox
    mock_runner_factory = MagicMock(spec=RunnerFactory)
    mock_runner_factory.get_runner.return_value = mock_runner

    orchestrator = HarnessOrchestrator(
        sandbox_factory=mock_sandbox_factory,
        runner_factory=mock_runner_factory,
    )

    sleep_log: list[float] = []

    async def _track_sleep(delay: float) -> None:
        sleep_log.append(delay)

    with patch.object(asyncio, "sleep", _track_sleep):
        result = await orchestrator.dispatch_with_retry(task)

    assert result.status == "passed"
    assert mock_runner.execute.call_count == 3
    assert fail_count == 3
    assert len(sleep_log) == 2
    assert sleep_log[0] == 2.0
    assert sleep_log[1] == 4.0


@pytest.mark.asyncio
async def test_harness_retry_on_failure_all_attempts_fail(
    _allow_local: None,
) -> None:
    """All 3 retry attempts fail -> OrchestratorError with MAX_RETRIES_EXCEEDED."""
    task = _make_api_task()

    mock_runner = MagicMock()
    mock_runner.setup = AsyncMock()
    mock_runner.execute = AsyncMock(side_effect=RuntimeError("Persistent failure"))
    mock_runner.collect_results = AsyncMock()
    mock_runner.teardown = AsyncMock()

    mock_sandbox = MagicMock(spec=ISandbox)
    mock_sandbox.create = AsyncMock(return_value="sandbox-retry-003")
    mock_sandbox.execute = AsyncMock()
    mock_sandbox.get_logs = AsyncMock(return_value="")
    mock_sandbox.get_artifacts = AsyncMock(return_value=[])
    mock_sandbox.destroy = AsyncMock()

    mock_sandbox_factory = MagicMock(spec=SandboxFactory)
    mock_sandbox_factory.create.return_value = mock_sandbox
    mock_runner_factory = MagicMock(spec=RunnerFactory)
    mock_runner_factory.get_runner.return_value = mock_runner

    orchestrator = HarnessOrchestrator(
        sandbox_factory=mock_sandbox_factory,
        runner_factory=mock_runner_factory,
    )

    sleep_log: list[float] = []

    async def _track_sleep(delay: float) -> None:
        sleep_log.append(delay)

    with (
        patch.object(asyncio, "sleep", _track_sleep),
        pytest.raises(OrchestratorError) as exc_info,
    ):
        await orchestrator.dispatch_with_retry(task)

    assert exc_info.value.code == "MAX_RETRIES_EXCEEDED"
    assert "failed after 3 attempts" in str(exc_info.value)
    assert mock_runner.execute.call_count == 3
    assert len(sleep_log) == 2
    assert sleep_log[0] == 2.0
    assert sleep_log[1] == 4.0


# ====================================================================
# test_harness_timeout_protection
# ====================================================================


@pytest.mark.asyncio
async def test_harness_timeout_protection_sandbox_execute(
    _allow_local: None,
) -> None:
    """LocalProcessSandbox.execute() raises SandboxTimeoutError when command exceeds timeout.

    1. Execute a long-running command (sleep 30) with 1s timeout
    2. Verify SandboxTimeoutError is raised
    3. Verify sandbox is still tracked for cleanup (destroy is idempotent)
    """
    if not shutil.which("sh"):
        pytest.skip("'sh' shell not available on this platform; skip sandbox timeout test")

    from testagent.harness.local_runner import LocalProcessSandbox

    sandbox = LocalProcessSandbox()
    sandbox_id = await sandbox.create({})

    with pytest.raises(SandboxTimeoutError) as exc_info:
        await sandbox.execute(sandbox_id, "sleep 30", timeout=1)

    assert "EXECUTION_TIMEOUT" in exc_info.value.code

    await sandbox.destroy(sandbox_id)

    assert sandbox_id not in sandbox._sandboxes


@pytest.mark.asyncio
async def test_harness_timeout_protection_dispatch(
    _allow_local: None,
) -> None:
    """HarnessOrchestrator.dispatch() handles runner timeout and cleans up resources.

    1. Create an API test task
    2. Runner.execute raises a timeout error
    3. Verify the sandbox is destroyed even when execution fails
    4. Verify runner teardown is called
    """
    task = _make_api_task()

    mock_runner = MagicMock()
    mock_runner.setup = AsyncMock()
    mock_runner.execute = AsyncMock(side_effect=TimeoutError("runner timed out"))
    mock_runner.collect_results = AsyncMock()
    mock_runner.teardown = AsyncMock()

    mock_sandbox = MagicMock(spec=ISandbox)
    mock_sandbox.create = AsyncMock(return_value="sandbox-timeout-001")
    mock_sandbox.execute = AsyncMock()
    mock_sandbox.get_logs = AsyncMock(return_value="")
    mock_sandbox.get_artifacts = AsyncMock(return_value=[])
    mock_sandbox.destroy = AsyncMock()

    mock_sandbox_factory = MagicMock(spec=SandboxFactory)
    mock_sandbox_factory.create.return_value = mock_sandbox
    mock_runner_factory = MagicMock(spec=RunnerFactory)
    mock_runner_factory.get_runner.return_value = mock_runner

    orchestrator = HarnessOrchestrator(
        sandbox_factory=mock_sandbox_factory,
        runner_factory=mock_runner_factory,
    )

    with pytest.raises(OrchestratorError) as exc_info:
        await orchestrator.dispatch(task)

    assert "timed out" in str(exc_info.value).lower() or "timeout" in str(exc_info.value).lower()
    mock_runner.teardown.assert_called_once()
    mock_sandbox.destroy.assert_called_once_with("sandbox-timeout-001")


@pytest.mark.asyncio
async def test_harness_timeout_protection_resource_profile_enforced(
    _allow_local: None,
) -> None:
    """Verify that ResourceProfile timeout values match ADR-004 specifications."""
    api_profile = RESOURCE_PROFILES["api_test"]
    assert api_profile.timeout == 60
    assert api_profile.cpus == 1
    assert api_profile.mem_limit == "512m"

    web_profile = RESOURCE_PROFILES["web_test"]
    assert web_profile.timeout == 120
    assert web_profile.cpus == 2
    assert web_profile.mem_limit == "2g"

    app_profile = RESOURCE_PROFILES["app_test"]
    assert app_profile.timeout == 180
    assert app_profile.cpus == 4
    assert app_profile.mem_limit == "4g"


# ====================================================================
# test_snapshot_resume
# ====================================================================


@pytest.mark.asyncio
async def test_snapshot_resume_save_and_load(tmp_path: Path) -> None:
    """Save an execution snapshot, then load it back and verify fields."""
    service = SnapshotService(storage_dir=str(tmp_path))

    await service.save(
        task_id="task-snap-001",
        status="running",
        checkpoint={"step": 3, "url": "http://example.com/page2", "retry_count": 1},
    )

    loaded = await service.load("task-snap-001")

    assert loaded is not None
    assert loaded.task_id == "task-snap-001"
    assert loaded.status == "running"
    assert loaded.progress == 0.3
    assert loaded.checkpoint == {"step": 3, "url": "http://example.com/page2", "retry_count": 1}
    assert loaded.created_at is not None


@pytest.mark.asyncio
async def test_snapshot_resume_from_checkpoint(tmp_path: Path) -> None:
    """Resume task execution from a saved checkpoint.

    1. Save a snapshot with mid-execution checkpoint data
    2. Simulate execution interruption (crash)
    3. Resume from the snapshot
    4. Verify checkpoint data guides the resumed execution
    """
    service = SnapshotService(storage_dir=str(tmp_path))

    checkpoint = {
        "step": 5,
        "url": "http://example.com/page5",
        "completed_actions": ["navigate", "click_login", "fill_form", "submit", "wait_dashboard"],
        "retry_count": 0,
        "cookies": {"session": "abc123"},
    }

    await service.save(
        task_id="task-resume-001",
        status="running",
        checkpoint=checkpoint,
    )

    snapshot = await service.resume("task-resume-001")

    assert snapshot is not None
    assert snapshot.task_id == "task-resume-001"
    assert snapshot.status == "running"
    assert snapshot.progress == 0.3

    cp: Any = snapshot.checkpoint
    assert cp["step"] == 5
    assert cp["url"] == "http://example.com/page5"
    assert len(cp["completed_actions"]) == 5
    assert cp["retry_count"] == 0
    assert cp["cookies"]["session"] == "abc123"

    resumed_url = cp["url"]
    resumed_step = cp["step"]
    completed = cp["completed_actions"]

    assert resumed_url == "http://example.com/page5"
    assert resumed_step == 5
    assert "submit" in completed


@pytest.mark.asyncio
async def test_snapshot_resume_nonexistent(tmp_path: Path) -> None:
    """Resume on nonexistent task returns None."""
    service = SnapshotService(storage_dir=str(tmp_path))
    snapshot = await service.resume("no-such-task")
    assert snapshot is None


@pytest.mark.asyncio
async def test_snapshot_resume_overwrites_existing(tmp_path: Path) -> None:
    """Save twice with same task_id -> later snapshot replaces earlier one."""
    service = SnapshotService(storage_dir=str(tmp_path))

    await service.save(
        task_id="task-overwrite",
        status="running",
        checkpoint={"step": 2},
    )

    await service.save(
        task_id="task-overwrite",
        status="passed",
        checkpoint={"step": 10},
    )

    loaded = await service.load("task-overwrite")

    assert loaded is not None
    assert loaded.status == "passed"
    assert loaded.checkpoint == {"step": 10}
    assert loaded.progress == 1.0


@pytest.mark.asyncio
async def test_snapshot_resume_retrying_status(tmp_path: Path) -> None:
    """Snapshot with retrying status is listed as incomplete and can be resumed."""
    service = SnapshotService(storage_dir=str(tmp_path))

    await service.save(
        task_id="task-retrying",
        status="retrying",
        checkpoint={"attempt": 2, "last_url": "http://example.com/retry"},
    )

    incomplete = await service.list_incomplete()
    task_ids = [s.task_id for s in incomplete]

    assert "task-retrying" in task_ids

    snapshot = await service.resume("task-retrying")
    assert snapshot is not None
    assert snapshot.status == "retrying"
    assert snapshot.progress == 0.5
    cp: Any = snapshot.checkpoint
    assert cp["attempt"] == 2


@pytest.mark.asyncio
async def test_snapshot_resume_full_lifecycle(tmp_path: Path) -> None:
    """Simulate a full task lifecycle with snapshots:

    1. Start execution -> save 'queued' snapshot
    2. Mid execution -> save 'running' snapshot with checkpoint
    3. Interruption -> clear in-memory state
    4. Resume from the 'running' snapshot
    5. Complete execution -> save 'passed' snapshot
    """
    service = SnapshotService(storage_dir=str(tmp_path))

    await service.save("task-lifecycle-001", "queued", {})
    snap = await service.load("task-lifecycle-001")
    assert snap is not None
    assert snap.status == "queued"
    assert snap.progress == 0.0

    await service.save(
        "task-lifecycle-001",
        "running",
        {"step": 4, "url": "http://example.com/checkout"},
    )
    snap = await service.load("task-lifecycle-001")
    assert snap is not None
    assert snap.status == "running"
    assert snap.progress == 0.3
    cp: Any = snap.checkpoint
    assert cp["step"] == 4

    snapshot = await service.resume("task-lifecycle-001")
    assert snapshot is not None
    cp = snapshot.checkpoint
    resume_step = cp["step"]
    resume_url = cp["url"]
    assert resume_step == 4
    assert resume_url == "http://example.com/checkout"

    remaining_steps = 10 - resume_step
    for i in range(resume_step + 1, resume_step + remaining_steps + 1):
        await service.save(
            "task-lifecycle-001",
            "running",
            {"step": i, "url": f"http://example.com/step{i}"},
        )

    await service.save("task-lifecycle-001", "passed", {"step": 10})

    final = await service.load("task-lifecycle-001")
    assert final is not None
    assert final.status == "passed"
    assert final.progress == 1.0

    incomplete = await service.list_incomplete()
    task_ids = [s.task_id for s in incomplete]
    assert "task-lifecycle-001" not in task_ids


@pytest.mark.asyncio
async def test_snapshot_resume_preserves_execution_context(tmp_path: Path) -> None:
    """Verify that an ExecutionSnapshot serialises and deserialises with all fields intact."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    snap = ExecutionSnapshot(
        task_id="task-ctx-001",
        status="running",
        progress=0.6,
        checkpoint={
            "page": 3,
            "auth_token": "tok_xxxx",
            "nested": {"key1": "val1", "key2": [1, 2, 3]},
            "empty_list": [],
        },
        created_at=now,
    )

    data = snap.to_dict()
    restored = ExecutionSnapshot.from_dict(data)

    assert restored.task_id == "task-ctx-001"
    assert restored.status == "running"
    assert restored.progress == 0.6
    rcp: Any = restored.checkpoint
    assert rcp["page"] == 3
    assert rcp["auth_token"] == "tok_xxxx"
    assert rcp["nested"]["key1"] == "val1"
    assert rcp["nested"]["key2"] == [1, 2, 3]
    assert rcp["empty_list"] == []
    assert restored.created_at == now


# ====================================================================
# test_harness_cleanup_guarantees
# ====================================================================


@pytest.mark.asyncio
async def test_harness_sandbox_destroy_on_dispatch_failure(_allow_local: None) -> None:
    """When dispatch fails mid-execution, sandbox.destroy() is still called.

    1. Runner.execute raises RuntimeError
    2. dispatch() propagates the error
    3. sandbox.destroy() was called regardless
    4. runners teardown() was called regardless
    """
    task = _make_api_task()

    mock_runner = MagicMock()
    mock_runner.setup = AsyncMock()
    mock_runner.execute = AsyncMock(side_effect=RuntimeError("execute failed"))
    mock_runner.collect_results = AsyncMock()
    mock_runner.teardown = AsyncMock()

    mock_sandbox = MagicMock(spec=ISandbox)
    mock_sandbox.create = AsyncMock(return_value="sandbox-cleanup-001")
    mock_sandbox.execute = AsyncMock()
    mock_sandbox.get_logs = AsyncMock(return_value="")
    mock_sandbox.get_artifacts = AsyncMock(return_value=[])
    mock_sandbox.destroy = AsyncMock()

    mock_sandbox_factory = MagicMock(spec=SandboxFactory)
    mock_sandbox_factory.create.return_value = mock_sandbox
    mock_runner_factory = MagicMock(spec=RunnerFactory)
    mock_runner_factory.get_runner.return_value = mock_runner

    orchestrator = HarnessOrchestrator(
        sandbox_factory=mock_sandbox_factory,
        runner_factory=mock_runner_factory,
    )

    with pytest.raises(OrchestratorError):
        await orchestrator.dispatch(task)

    mock_runner.teardown.assert_called_once()
    mock_sandbox.destroy.assert_called_once_with("sandbox-cleanup-001")


@pytest.mark.asyncio
async def test_harness_isolation_level_fallback(_allow_local: None) -> None:
    """Unknown task_type without explicit isolation level falls back to LOCAL."""
    task = TestTask(
        id="task-fallback-001",
        plan_id="plan-integ-001",
        task_type="unknown_type",
        isolation_level="",
        priority=1,
        status="queued",
        retry_count=0,
        task_config={},
    )

    orchestrator = HarnessOrchestrator()
    level = orchestrator.decide_isolation(task)

    assert level == IsolationLevel.LOCAL


@pytest.mark.asyncio
async def test_harness_resource_profile_constants() -> None:
    """Verify ResourceProfile constants match ADR-004 specifications."""
    assert "api_test" in RESOURCE_PROFILES
    assert "web_test" in RESOURCE_PROFILES
    assert "app_test" in RESOURCE_PROFILES

    assert RESOURCE_PROFILES["api_test"].cpus == 1
    assert RESOURCE_PROFILES["api_test"].mem_limit == "512m"
    assert RESOURCE_PROFILES["api_test"].timeout == 60

    assert RESOURCE_PROFILES["web_test"].cpus == 2
    assert RESOURCE_PROFILES["web_test"].mem_limit == "2g"
    assert RESOURCE_PROFILES["web_test"].timeout == 120

    assert RESOURCE_PROFILES["app_test"].cpus == 4
    assert RESOURCE_PROFILES["app_test"].mem_limit == "4g"
    assert RESOURCE_PROFILES["app_test"].timeout == 180
