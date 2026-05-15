from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from testagent.harness.snapshot import (
    ExecutionSnapshot,
    SnapshotError,
    SnapshotNotFoundError,
    SnapshotService,
)

# ====================================================================
# 集成测试: 细粒度步骤快照保存
# ====================================================================


class TestStepSnapshotIntegration:
    """细粒度步骤快照保存——集成测试"""

    @pytest_asyncio.fixture()
    async def service(self, tmp_path: Path) -> SnapshotService:
        return SnapshotService(storage_dir=str(tmp_path))

    async def test_step_by_step_execution_with_snapshots(self, service: SnapshotService) -> None:
        snapshot = ExecutionSnapshot(
            task_id="task-step-by-step",
            status="running",
            session_id="sess-integration",
            completed_steps=[],
            remaining_steps=["init", "login", "search", "checkout", "verify"],
            resource_state={"sandbox_id": "sbx-001"},
            checkpoint={"test_type": "web_test"},
        )
        await service.save_full_snapshot(snapshot)

        step_results = {
            "init": {"status": "passed", "duration_ms": 50},
            "login": {"status": "passed", "duration_ms": 200},
            "search": {"status": "passed", "duration_ms": 300},
            "checkout": {"status": "passed", "duration_ms": 400},
            "verify": {"status": "passed", "duration_ms": 100},
        }

        for step_name, step_result in step_results.items():
            await service.save_step_completion(
                task_id="task-step-by-step",
                step_id=step_name,
                result=step_result,
            )

        loaded = await service.load("task-step-by-step")
        assert loaded is not None
        assert loaded.completed_steps == ["init", "login", "search", "checkout", "verify"]
        assert loaded.remaining_steps == []
        assert loaded.progress == 1.0
        for step_name, expected_result in step_results.items():
            assert loaded.intermediate_results[step_name] == expected_result

    async def test_step_snapshot_file_persistence(self, service: SnapshotService) -> None:
        await service.save_step_completion(
            task_id="task-file-persist",
            step_id="step-1",
            result={"ok": True, "data": [1, 2, 3]},
            session_id="sess-file",
        )

        path = service._snapshot_path("task-file-persist")
        assert path.exists()

        with open(path, encoding="utf-8") as f:
            raw = json.load(f)

        assert raw["task_id"] == "task-file-persist"
        assert raw["session_id"] == "sess-file"
        assert raw["completed_steps"] == ["step-1"]
        assert raw["intermediate_results"]["step-1"]["data"] == [1, 2, 3]

    async def test_step_snapshot_preserves_resource_state(self, service: SnapshotService) -> None:
        snapshot = ExecutionSnapshot(
            task_id="task-resource",
            status="running",
            resource_state={
                "sandbox_id": "sbx-resource",
                "container_id": "ctr-resource",
                "network": "test-net",
            },
            completed_steps=["step-1"],
            remaining_steps=["step-2", "step-3"],
            checkpoint={},
        )
        await service.save_full_snapshot(snapshot)

        await service.save_step_completion(
            task_id="task-resource",
            step_id="step-2",
            result={"status": "passed"},
        )

        loaded = await service.load("task-resource")
        assert loaded is not None
        assert loaded.resource_state["sandbox_id"] == "sbx-resource"
        assert loaded.resource_state["container_id"] == "ctr-resource"


# ====================================================================
# 集成测试: 从快照恢复执行
# ====================================================================


class TestResumeFromSnapshotIntegration:
    """从快照恢复执行——集成测试"""

    @pytest_asyncio.fixture()
    async def service(self, tmp_path: Path) -> SnapshotService:
        return SnapshotService(storage_dir=str(tmp_path))

    async def test_resume_restores_full_context(self, service: SnapshotService) -> None:
        snapshot = ExecutionSnapshot(
            task_id="task-resume-full",
            status="running",
            session_id="sess-resume",
            completed_steps=["step-1", "step-2"],
            remaining_steps=["step-3", "step-4", "step-5"],
            intermediate_results={
                "step-1": {"assertions": 10, "passed": 10},
                "step-2": {"assertions": 5, "passed": 5},
            },
            resource_state={
                "sandbox_id": "sbx-resume",
                "container_id": "ctr-resume",
                "docker_image": "testagent/web-runner:latest",
            },
            checkpoint={"current_step": "step-3", "total_steps": 5},
        )
        await service.save_full_snapshot(snapshot)

        context = await service.resume_from_snapshot("task-resume-full")

        assert context["resume_from_step"] == "step-3"
        assert context["session_id"] == "sess-resume"
        assert context["completed_steps"] == ["step-1", "step-2"]
        assert context["remaining_steps"] == ["step-3", "step-4", "step-5"]
        assert context["intermediate_results"]["step-1"]["assertions"] == 10
        assert context["resource_state"]["docker_image"] == "testagent/web-runner:latest"

    async def test_resume_after_partial_step_completion(self, service: SnapshotService) -> None:
        snapshot = ExecutionSnapshot(
            task_id="task-partial",
            status="running",
            session_id="sess-partial",
            completed_steps=["step-1"],
            remaining_steps=["step-2", "step-3"],
            intermediate_results={"step-1": {"ok": True}},
            resource_state={"sandbox_id": "sbx-partial"},
            checkpoint={},
        )
        await service.save_full_snapshot(snapshot)

        await service.resume_from_snapshot("task-partial")

        await service.save_step_completion(
            task_id="task-partial",
            step_id="step-2",
            result={"ok": True, "recovered": True},
        )

        loaded = await service.load("task-partial")
        assert loaded is not None
        assert "step-2" in loaded.completed_steps
        assert loaded.intermediate_results["step-2"]["recovered"] is True

    async def test_resume_terminal_status_raises_error(self, service: SnapshotService) -> None:
        snapshot = ExecutionSnapshot(
            task_id="task-done",
            status="passed",
            completed_steps=["step-1", "step-2"],
            remaining_steps=[],
            checkpoint={},
        )
        await service.save_full_snapshot(snapshot)

        with pytest.raises(SnapshotError) as exc_info:
            await service.resume_from_snapshot("task-done")

        assert exc_info.value.code == "SNAPSHOT_TERMINAL"

    async def test_resume_nonexistent_raises_not_found(self, service: SnapshotService) -> None:
        with pytest.raises(SnapshotNotFoundError) as exc_info:
            await service.resume_from_snapshot("ghost-task")

        assert exc_info.value.code == "SNAPSHOT_NOT_FOUND"


# ====================================================================
# 集成测试: 跨重启恢复
# ====================================================================


class TestCrossRestartRecoveryIntegration:
    """跨重启恢复——集成测试"""

    async def test_recovery_after_service_restart(self, tmp_path: Path) -> None:
        service_v1 = SnapshotService(storage_dir=str(tmp_path))

        snapshot = ExecutionSnapshot(
            task_id="task-restart",
            status="running",
            session_id="sess-restart",
            completed_steps=["step-1", "step-2"],
            remaining_steps=["step-3", "step-4"],
            intermediate_results={
                "step-1": {"status": "passed", "duration_ms": 100},
                "step-2": {"status": "passed", "duration_ms": 200},
            },
            resource_state={"sandbox_id": "sbx-restart", "container_id": "ctr-restart"},
            checkpoint={},
        )
        await service_v1.save_full_snapshot(snapshot)

        service_v2 = SnapshotService(storage_dir=str(tmp_path))

        context = await service_v2.resume_from_snapshot("task-restart")
        assert context["resume_from_step"] == "step-3"
        assert context["session_id"] == "sess-restart"
        assert context["intermediate_results"]["step-1"]["duration_ms"] == 100
        assert context["resource_state"]["container_id"] == "ctr-restart"

        await service_v2.save_step_completion(
            task_id="task-restart",
            step_id="step-3",
            result={"status": "passed", "duration_ms": 150},
        )

        service_v3 = SnapshotService(storage_dir=str(tmp_path))
        loaded = await service_v3.load("task-restart")
        assert loaded is not None
        assert "step-3" in loaded.completed_steps
        assert loaded.intermediate_results["step-3"]["duration_ms"] == 150

    async def test_multiple_interruptions_recovery(self, tmp_path: Path) -> None:
        snapshot = ExecutionSnapshot(
            task_id="task-multi-interrupt",
            status="running",
            session_id="sess-multi",
            completed_steps=[],
            remaining_steps=["step-1", "step-2", "step-3", "step-4", "step-5"],
            resource_state={"sandbox_id": "sbx-multi"},
            checkpoint={},
        )

        service = SnapshotService(storage_dir=str(tmp_path))
        await service.save_full_snapshot(snapshot)

        interruptions = [
            ("step-1", {"status": "passed", "run": 1}),
            ("step-2", {"status": "passed", "run": 1}),
        ]
        for step_id, result in interruptions:
            await service.save_step_completion(
                task_id="task-multi-interrupt",
                step_id=step_id,
                result=result,
            )

        service = SnapshotService(storage_dir=str(tmp_path))
        context = await service.resume_from_snapshot("task-multi-interrupt")
        assert context["resume_from_step"] == "step-3"

        await service.save_step_completion(
            task_id="task-multi-interrupt",
            step_id="step-3",
            result={"status": "passed", "run": 2},
        )

        service = SnapshotService(storage_dir=str(tmp_path))
        context = await service.resume_from_snapshot("task-multi-interrupt")
        assert context["resume_from_step"] == "step-4"

        await service.save_step_completion(
            task_id="task-multi-interrupt",
            step_id="step-4",
            result={"status": "passed", "run": 3},
        )
        await service.save_step_completion(
            task_id="task-multi-interrupt",
            step_id="step-5",
            result={"status": "passed", "run": 3},
        )

        loaded = await service.load("task-multi-interrupt")
        assert loaded is not None
        assert len(loaded.completed_steps) == 5
        assert len(loaded.remaining_steps) == 0
        assert loaded.progress == 1.0


# ====================================================================
# 集成测试: 过期快照清理
# ====================================================================


class TestCleanupIntegration:
    """过期快照清理——集成测试"""

    @pytest_asyncio.fixture()
    async def service(self, tmp_path: Path) -> SnapshotService:
        return SnapshotService(storage_dir=str(tmp_path))

    async def test_cleanup_respects_retention_days(self, service: SnapshotService) -> None:
        snapshots = [
            ExecutionSnapshot(
                task_id=f"task-day-{i}",
                status="running",
                created_at=datetime.now(UTC) - timedelta(days=i),
                updated_at=datetime.now(UTC) - timedelta(days=i),
                checkpoint={"age_days": i},
            )
            for i in [1, 5, 10, 20, 30, 89, 90, 91]
        ]
        for snap in snapshots:
            await service.save_full_snapshot(snap)

        cleaned = await service.cleanup_old_snapshots(days=7)

        assert cleaned == 6

        for i in [1, 5]:
            assert await service.load(f"task-day-{i}") is not None

        for i in [10, 20, 30, 89, 90, 91]:
            assert await service.load(f"task-day-{i}") is None

    async def test_cleanup_default_90_days(self, service: SnapshotService) -> None:
        snapshot = ExecutionSnapshot(
            task_id="task-91-days",
            status="running",
            created_at=datetime.now(UTC) - timedelta(days=91),
            updated_at=datetime.now(UTC) - timedelta(days=91),
            checkpoint={},
        )
        await service.save_full_snapshot(snapshot)

        cleaned = await service.cleanup_old_snapshots()
        assert cleaned == 1
        assert await service.load("task-91-days") is None

    async def test_cleanup_preserves_recent_snapshots(self, service: SnapshotService) -> None:
        snapshot = ExecutionSnapshot(
            task_id="task-recent",
            status="running",
            checkpoint={},
        )
        await service.save_full_snapshot(snapshot)

        cleaned = await service.cleanup_old_snapshots(days=7)
        assert cleaned == 0
        assert await service.load("task-recent") is not None

    async def test_cleanup_terminal_snapshots(self, service: SnapshotService) -> None:
        old_passed = ExecutionSnapshot(
            task_id="old-passed",
            status="passed",
            created_at=datetime.now(UTC) - timedelta(days=10),
            updated_at=datetime.now(UTC) - timedelta(days=10),
            completed_steps=["step-1"],
            remaining_steps=[],
            checkpoint={},
        )
        await service.save_full_snapshot(old_passed)

        cleaned = await service.cleanup_old_snapshots(days=7)
        assert cleaned == 1
        assert await service.load("old-passed") is None

    async def test_cleanup_mixed_corrupted_and_valid(self, service: SnapshotService) -> None:
        service._ensure_dir()

        old_snap = ExecutionSnapshot(
            task_id="old-valid",
            status="running",
            created_at=datetime.now(UTC) - timedelta(days=10),
            updated_at=datetime.now(UTC) - timedelta(days=10),
            checkpoint={},
        )
        await service.save_full_snapshot(old_snap)

        corrupted_path = Path(service._storage_dir) / "corrupted_old.json"
        corrupted_path.write_text("NOT VALID JSON AT ALL", encoding="utf-8")

        cleaned = await service.cleanup_old_snapshots(days=7)
        assert cleaned == 2
        assert await service.load("old-valid") is None
        assert not corrupted_path.exists()


# ====================================================================
# 集成测试: 断点续跑成功率
# ====================================================================


class TestBreakpointResumeSuccessRate:
    """断点续跑成功率 100% 验证——集成测试"""

    @pytest_asyncio.fixture()
    async def service(self, tmp_path: Path) -> SnapshotService:
        return SnapshotService(storage_dir=str(tmp_path))

    async def test_resume_success_rate_100_percent(self, service: SnapshotService) -> None:
        scenarios: list[dict[str, object]] = [
            {
                "task_id": "scenario-1-empty-steps",
                "status": "running",
                "completed_steps": [],
                "remaining_steps": ["step-1"],
                "resource_state": {},
                "intermediate_results": {},
            },
            {
                "task_id": "scenario-2-partial",
                "status": "running",
                "completed_steps": ["step-1"],
                "remaining_steps": ["step-2"],
                "resource_state": {"sandbox_id": "sbx-2"},
                "intermediate_results": {"step-1": {"ok": True}},
            },
            {
                "task_id": "scenario-3-near-complete",
                "status": "running",
                "completed_steps": ["s1", "s2", "s3", "s4"],
                "remaining_steps": ["s5"],
                "resource_state": {"container_id": "ctr-3"},
                "intermediate_results": {"s1": {}, "s2": {}, "s3": {}, "s4": {}},
            },
            {
                "task_id": "scenario-4-retrying",
                "status": "retrying",
                "completed_steps": ["step-1"],
                "remaining_steps": ["step-2"],
                "resource_state": {},
                "intermediate_results": {"step-1": {"ok": True}},
            },
            {
                "task_id": "scenario-5-multiple-resources",
                "status": "running",
                "completed_steps": ["init"],
                "remaining_steps": ["exec", "cleanup"],
                "resource_state": {"sandbox_id": "sbx-5", "container_id": "ctr-5", "network": "net-5"},
                "intermediate_results": {"init": {"ok": True}},
            },
        ]

        for s in scenarios:
            snapshot = ExecutionSnapshot(
                task_id=str(s["task_id"]),
                status=str(s["status"]),
                session_id=f"sess-{s['task_id']}",
                completed_steps=list(s["completed_steps"]),
                remaining_steps=list(s["remaining_steps"]),
                resource_state=dict(s["resource_state"]),
                intermediate_results=dict(s["intermediate_results"]),
                checkpoint={},
            )
            await service.save_full_snapshot(snapshot)

        success = 0
        total = len(scenarios)
        for s in scenarios:
            try:
                context = await service.resume_from_snapshot(str(s["task_id"]))
                if context.get("resume_from_step") is not None or context.get("resume_from_step") == "":
                    success += 1
            except (SnapshotNotFoundError, SnapshotError):
                pass

        assert success == total
        assert success / total == 1.0

    async def test_large_scale_resume_reliability(self, service: SnapshotService) -> None:
        num_tasks = 50
        for i in range(num_tasks):
            completed_count = i % 6
            snapshot = ExecutionSnapshot(
                task_id=f"task-scale-{i}",
                status="running",
                session_id=f"sess-scale-{i}",
                completed_steps=[f"step-{j}" for j in range(completed_count)],
                remaining_steps=[f"step-{j}" for j in range(completed_count, 6)],
                intermediate_results={f"step-{j}": {"ok": True} for j in range(completed_count)},
                resource_state={"sandbox_id": f"sbx-scale-{i}"},
                checkpoint={},
            )
            await service.save_full_snapshot(snapshot)

        success = 0
        for i in range(num_tasks):
            try:
                context = await service.resume_from_snapshot(f"task-scale-{i}")
                assert "resume_from_step" in context
                assert "resource_state" in context
                assert "intermediate_results" in context
                success += 1
            except (SnapshotNotFoundError, SnapshotError):
                pass

        assert success == num_tasks

    async def test_full_lifecycle_with_cleanup(self, service: SnapshotService) -> None:
        snapshot = ExecutionSnapshot(
            task_id="task-lifecycle",
            status="running",
            session_id="sess-lifecycle",
            completed_steps=[],
            remaining_steps=["step-1", "step-2", "step-3"],
            resource_state={"sandbox_id": "sbx-lifecycle"},
            checkpoint={},
        )
        await service.save_full_snapshot(snapshot)

        await service.save_step_completion("task-lifecycle", "step-1", {"status": "passed"})
        await service.save_step_completion("task-lifecycle", "step-2", {"status": "passed"})

        context = await service.resume_from_snapshot("task-lifecycle")
        assert context["resume_from_step"] == "step-3"

        await service.save_step_completion("task-lifecycle", "step-3", {"status": "passed"})

        loaded = await service.load("task-lifecycle")
        assert loaded is not None
        assert loaded.progress == 1.0
        assert len(loaded.completed_steps) == 3
        assert len(loaded.remaining_steps) == 0

        cleaned = await service.cleanup_old_snapshots(days=7)
        assert cleaned == 0

        assert await service.load("task-lifecycle") is not None


# ====================================================================
# 集成测试: 文件系统 + Redis 双层持久化
# ====================================================================


class TestDualPersistenceIntegration:
    """文件系统 + Redis 双层持久化——集成测试"""

    async def test_file_system_primary_persistence(self, tmp_path: Path) -> None:
        service = SnapshotService(storage_dir=str(tmp_path))

        snapshot = ExecutionSnapshot(
            task_id="task-fs-primary",
            status="running",
            session_id="sess-fs",
            completed_steps=["step-1"],
            remaining_steps=["step-2"],
            intermediate_results={"step-1": {"ok": True}},
            resource_state={"sandbox_id": "sbx-fs"},
            checkpoint={},
        )
        await service.save_full_snapshot(snapshot)

        assert service._snapshot_path("task-fs-primary").exists()

        new_service = SnapshotService(storage_dir=str(tmp_path))
        loaded = await new_service.load("task-fs-primary")
        assert loaded is not None
        assert loaded.completed_steps == ["step-1"]

    async def test_redis_persistence_when_available(self, tmp_path: Path) -> None:
        mock_redis = self._create_mock_redis()
        service = SnapshotService(storage_dir=str(tmp_path), redis_client=mock_redis)

        snapshot = ExecutionSnapshot(
            task_id="task-redis-persist",
            status="running",
            session_id="sess-redis",
            completed_steps=["step-1"],
            remaining_steps=["step-2"],
            checkpoint={},
        )
        await service.save_full_snapshot(snapshot)

        assert mock_redis.hset.called
        assert mock_redis.xadd.called

    async def test_redis_failure_file_remains_primary(self, tmp_path: Path) -> None:
        mock_redis = self._create_failing_mock_redis()
        service = SnapshotService(storage_dir=str(tmp_path), redis_client=mock_redis)

        snapshot = ExecutionSnapshot(
            task_id="task-redis-fail",
            status="running",
            checkpoint={},
        )
        await service.save_full_snapshot(snapshot)

        loaded = await service.load("task-redis-fail")
        assert loaded is not None
        assert loaded.task_id == "task-redis-fail"

    async def test_cleanup_deletes_from_both_layers(self, tmp_path: Path) -> None:
        mock_redis = self._create_mock_redis()
        service = SnapshotService(storage_dir=str(tmp_path), redis_client=mock_redis)

        snapshot = ExecutionSnapshot(
            task_id="task-cleanup-both",
            status="running",
            created_at=datetime.now(UTC) - timedelta(days=10),
            updated_at=datetime.now(UTC) - timedelta(days=10),
            checkpoint={},
        )
        await service.save_full_snapshot(snapshot)

        cleaned = await service.cleanup_old_snapshots(days=7)
        assert cleaned == 1
        assert not service._snapshot_path("task-cleanup-both").exists()

    def _create_mock_redis(self) -> object:
        from unittest.mock import MagicMock

        redis = MagicMock()
        redis.hset = MagicMock()
        redis.hgetall = MagicMock(return_value={})
        redis.delete = MagicMock()
        redis.xadd = MagicMock()
        return redis

    def _create_failing_mock_redis(self) -> object:
        from unittest.mock import MagicMock

        redis = MagicMock()
        redis.hset = MagicMock(side_effect=ConnectionError("Redis unavailable"))
        redis.xadd = MagicMock(side_effect=ConnectionError("Redis unavailable"))
        redis.hgetall = MagicMock(side_effect=ConnectionError("Redis unavailable"))
        redis.delete = MagicMock(side_effect=ConnectionError("Redis unavailable"))
        return redis
