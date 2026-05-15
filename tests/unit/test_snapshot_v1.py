from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from testagent.harness.snapshot import (
    ExecutionSnapshot,
    SnapshotError,
    SnapshotNotFoundError,
    SnapshotService,
)

# ====================================================================
# TestExecutionSnapshotV1 — V1.0 增强 ExecutionSnapshot
# ====================================================================


class TestExecutionSnapshotV1:
    """ExecutionSnapshot V1.0 — 新增字段 + 序列化/反序列化"""

    def test_v1_fields_default_values(self) -> None:
        snap = ExecutionSnapshot(
            task_id="task-001",
            status="running",
        )
        assert snap.session_id == ""
        assert snap.completed_steps == []
        assert snap.remaining_steps == []
        assert snap.intermediate_results == {}
        assert snap.resource_state == {}
        assert snap.updated_at == snap.created_at

    def test_v1_fields_custom_values(self) -> None:
        now = datetime.now(UTC)
        snap = ExecutionSnapshot(
            task_id="task-002",
            status="running",
            session_id="sess-001",
            progress=0.5,
            checkpoint={"step": 2},
            completed_steps=["step-1", "step-2"],
            remaining_steps=["step-3", "step-4"],
            intermediate_results={"step-1": {"status": "passed"}, "step-2": {"status": "passed"}},
            resource_state={"sandbox_id": "sbx-abc", "container_id": "ctr-xyz"},
            created_at=now,
            updated_at=now + timedelta(seconds=10),
        )
        assert snap.session_id == "sess-001"
        assert snap.completed_steps == ["step-1", "step-2"]
        assert snap.remaining_steps == ["step-3", "step-4"]
        assert snap.intermediate_results == {"step-1": {"status": "passed"}, "step-2": {"status": "passed"}}
        assert snap.resource_state == {"sandbox_id": "sbx-abc", "container_id": "ctr-xyz"}
        assert snap.updated_at > snap.created_at

    def test_to_dict_v1_roundtrip(self) -> None:
        now = datetime.now(UTC)
        snap = ExecutionSnapshot(
            task_id="task-003",
            status="running",
            session_id="sess-003",
            progress=0.4,
            checkpoint={"current": "step-2"},
            completed_steps=["step-1"],
            remaining_steps=["step-2", "step-3"],
            intermediate_results={"step-1": {"assertions": 5}},
            resource_state={"container_id": "docker-123"},
            created_at=now,
            updated_at=now + timedelta(seconds=5),
        )

        data = snap.to_dict()
        assert data["session_id"] == "sess-003"
        assert data["completed_steps"] == ["step-1"]
        assert data["remaining_steps"] == ["step-2", "step-3"]
        assert data["intermediate_results"] == {"step-1": {"assertions": 5}}
        assert data["resource_state"] == {"container_id": "docker-123"}
        assert data["updated_at"] == (now + timedelta(seconds=5)).isoformat()

        restored = ExecutionSnapshot.from_dict(data)
        assert restored.task_id == snap.task_id
        assert restored.session_id == snap.session_id
        assert restored.completed_steps == snap.completed_steps
        assert restored.remaining_steps == snap.remaining_steps
        assert restored.intermediate_results == snap.intermediate_results
        assert restored.resource_state == snap.resource_state
        assert restored.created_at == snap.created_at
        assert restored.updated_at == snap.updated_at

    def test_from_dict_v1_missing_new_fields(self) -> None:
        data: dict[str, object] = {
            "task_id": "task-004",
            "status": "queued",
            "progress": 0.0,
            "checkpoint": {},
        }
        snap = ExecutionSnapshot.from_dict(data)
        assert snap.session_id == ""
        assert snap.completed_steps == []
        assert snap.remaining_steps == []
        assert snap.intermediate_results == {}
        assert snap.resource_state == {}
        assert snap.updated_at == snap.created_at

    def test_from_dict_v1_missing_updated_at_falls_back_to_created_at(self) -> None:
        now = datetime.now(UTC)
        data: dict[str, object] = {
            "task_id": "task-005",
            "status": "running",
            "progress": 0.3,
            "checkpoint": {},
            "created_at": now.isoformat(),
        }
        snap = ExecutionSnapshot.from_dict(data)
        assert snap.updated_at == snap.created_at

    def test_compute_progress_with_steps(self) -> None:
        snap = ExecutionSnapshot(
            task_id="task-006",
            status="running",
            completed_steps=["step-1", "step-2"],
            remaining_steps=["step-3", "step-4", "step-5"],
        )
        assert snap.compute_progress() == 2 / 5

    def test_compute_progress_no_steps_falls_back_to_status(self) -> None:
        snap = ExecutionSnapshot(
            task_id="task-007",
            status="running",
        )
        assert snap.compute_progress() == 0.3

    def test_compute_progress_all_steps_completed(self) -> None:
        snap = ExecutionSnapshot(
            task_id="task-008",
            status="running",
            completed_steps=["step-1", "step-2", "step-3"],
            remaining_steps=[],
        )
        assert snap.compute_progress() == 1.0

    def test_is_terminal(self) -> None:
        for status in ("passed", "failed", "skipped", "completed"):
            snap = ExecutionSnapshot(task_id="t", status=status)
            assert snap.is_terminal() is True

        for status in ("queued", "running", "retrying"):
            snap = ExecutionSnapshot(task_id="t", status=status)
            assert snap.is_terminal() is False

    def test_repr(self) -> None:
        snap = ExecutionSnapshot(
            task_id="task-009",
            status="running",
            completed_steps=["s1"],
            remaining_steps=["s2", "s3"],
        )
        r = repr(snap)
        assert "task-009" in r
        assert "running" in r
        assert "completed=1" in r
        assert "remaining=2" in r


# ====================================================================
# TestSnapshotServiceV1 — V1.0 增强 SnapshotService
# ====================================================================


class TestSnapshotServiceV1:
    """SnapshotService V1.0 — 细粒度快照/恢复/清理"""

    @pytest.fixture()
    def service(self, tmp_path: Path) -> SnapshotService:
        return SnapshotService(storage_dir=str(tmp_path))

    @pytest.fixture()
    def service_with_mock_redis(self, tmp_path: Path) -> SnapshotService:
        mock_redis = MagicMock()
        mock_redis.hset = MagicMock()
        mock_redis.hgetall = MagicMock(return_value={})
        mock_redis.delete = MagicMock()
        mock_redis.xadd = MagicMock()
        return SnapshotService(storage_dir=str(tmp_path), redis_client=mock_redis)

    # --- save_step_completion ---

    async def test_save_step_completion_creates_new_snapshot(self, service: SnapshotService) -> None:
        await service.save_step_completion(
            task_id="task-001",
            step_id="step-2",
            result={"status": "passed", "duration_ms": 120},
            session_id="sess-001",
        )

        loaded = await service.load("task-001")
        assert loaded is not None
        assert loaded.session_id == "sess-001"
        assert "step-2" in loaded.completed_steps
        assert loaded.intermediate_results["step-2"] == {"status": "passed", "duration_ms": 120}
        assert loaded.status == "running"

    async def test_save_step_completion_updates_existing_snapshot(self, service: SnapshotService) -> None:
        await service.save(
            task_id="task-002",
            status="running",
            checkpoint={"current": "step-1"},
        )

        await service.save_step_completion(
            task_id="task-002",
            step_id="step-1",
            result={"status": "passed"},
        )

        loaded = await service.load("task-002")
        assert loaded is not None
        assert "step-1" in loaded.completed_steps
        assert loaded.intermediate_results["step-1"] == {"status": "passed"}

    async def test_save_step_completion_moves_step_from_remaining(self, service: SnapshotService) -> None:
        snapshot = ExecutionSnapshot(
            task_id="task-003",
            status="running",
            completed_steps=["step-1"],
            remaining_steps=["step-2", "step-3"],
            checkpoint={},
        )
        await service.save_full_snapshot(snapshot)

        await service.save_step_completion(
            task_id="task-003",
            step_id="step-2",
            result={"status": "passed"},
        )

        loaded = await service.load("task-003")
        assert loaded is not None
        assert "step-1" in loaded.completed_steps
        assert "step-2" in loaded.completed_steps
        assert "step-2" not in loaded.remaining_steps
        assert loaded.remaining_steps == ["step-3"]

    async def test_save_step_completion_updates_progress(self, service: SnapshotService) -> None:
        snapshot = ExecutionSnapshot(
            task_id="task-004",
            status="running",
            completed_steps=["step-1"],
            remaining_steps=["step-2", "step-3", "step-4"],
            checkpoint={},
        )
        await service.save_full_snapshot(snapshot)

        await service.save_step_completion(
            task_id="task-004",
            step_id="step-2",
            result={"status": "passed"},
        )

        loaded = await service.load("task-004")
        assert loaded is not None
        assert loaded.progress == 2 / 4

    async def test_save_step_completion_updates_updated_at(self, service: SnapshotService) -> None:
        snapshot = ExecutionSnapshot(
            task_id="task-005",
            status="running",
            checkpoint={},
        )
        original_updated = snapshot.updated_at
        await service.save_full_snapshot(snapshot)

        import asyncio

        await asyncio.sleep(0.05)

        await service.save_step_completion(
            task_id="task-005",
            step_id="step-1",
            result={"status": "passed"},
        )

        loaded = await service.load("task-005")
        assert loaded is not None
        assert loaded.updated_at > original_updated

    async def test_save_step_completion_no_duplicate_in_completed(self, service: SnapshotService) -> None:
        await service.save_step_completion(
            task_id="task-006",
            step_id="step-1",
            result={"status": "passed"},
        )
        await service.save_step_completion(
            task_id="task-006",
            step_id="step-1",
            result={"status": "passed", "retried": True},
        )

        loaded = await service.load("task-006")
        assert loaded is not None
        assert loaded.completed_steps.count("step-1") == 1

    # --- resume_from_snapshot ---

    async def test_resume_from_snapshot_success(self, service: SnapshotService) -> None:
        snapshot = ExecutionSnapshot(
            task_id="task-010",
            status="running",
            session_id="sess-010",
            completed_steps=["step-1", "step-2"],
            remaining_steps=["step-3", "step-4"],
            intermediate_results={"step-1": {"ok": True}, "step-2": {"ok": True}},
            resource_state={"sandbox_id": "sbx-123", "container_id": "ctr-456"},
            checkpoint={},
        )
        await service.save_full_snapshot(snapshot)

        context = await service.resume_from_snapshot("task-010")

        assert context["resume_from_step"] == "step-3"
        assert context["session_id"] == "sess-010"
        assert context["completed_steps"] == ["step-1", "step-2"]
        assert context["remaining_steps"] == ["step-3", "step-4"]
        assert context["resource_state"] == {"sandbox_id": "sbx-123", "container_id": "ctr-456"}
        assert context["intermediate_results"]["step-1"] == {"ok": True}

    async def test_resume_from_snapshot_not_found(self, service: SnapshotService) -> None:
        with pytest.raises(SnapshotNotFoundError) as exc_info:
            await service.resume_from_snapshot("nonexistent-task")

        assert exc_info.value.code == "SNAPSHOT_NOT_FOUND"

    async def test_resume_from_snapshot_terminal_status(self, service: SnapshotService) -> None:
        await service.save("task-011", "passed", {"final": True})

        with pytest.raises(SnapshotError) as exc_info:
            await service.resume_from_snapshot("task-011")

        assert exc_info.value.code == "SNAPSHOT_TERMINAL"

    async def test_resume_from_snapshot_no_remaining_steps(self, service: SnapshotService) -> None:
        snapshot = ExecutionSnapshot(
            task_id="task-012",
            status="running",
            completed_steps=["step-1", "step-2", "step-3"],
            remaining_steps=[],
            checkpoint={},
        )
        await service.save_full_snapshot(snapshot)

        context = await service.resume_from_snapshot("task-012")
        assert context["resume_from_step"] == ""

    # --- cleanup_old_snapshots ---

    async def test_cleanup_old_snapshots_removes_expired(self, service: SnapshotService) -> None:
        old_snapshot = ExecutionSnapshot(
            task_id="old-task",
            status="running",
            created_at=datetime.now(UTC) - timedelta(days=10),
            updated_at=datetime.now(UTC) - timedelta(days=10),
            checkpoint={},
        )
        await service.save_full_snapshot(old_snapshot)

        recent_snapshot = ExecutionSnapshot(
            task_id="recent-task",
            status="running",
            checkpoint={},
        )
        await service.save_full_snapshot(recent_snapshot)

        cleaned = await service.cleanup_old_snapshots(days=7)

        assert cleaned == 1
        assert await service.load("old-task") is None
        assert await service.load("recent-task") is not None

    async def test_cleanup_old_snapshots_nothing_expired(self, service: SnapshotService) -> None:
        snapshot = ExecutionSnapshot(
            task_id="fresh-task",
            status="running",
            checkpoint={},
        )
        await service.save_full_snapshot(snapshot)

        cleaned = await service.cleanup_old_snapshots(days=7)
        assert cleaned == 0

    async def test_cleanup_old_snapshots_default_retention(self, service: SnapshotService) -> None:
        old_snapshot = ExecutionSnapshot(
            task_id="very-old-task",
            status="running",
            created_at=datetime.now(UTC) - timedelta(days=100),
            updated_at=datetime.now(UTC) - timedelta(days=100),
            checkpoint={},
        )
        await service.save_full_snapshot(old_snapshot)

        cleaned = await service.cleanup_old_snapshots()
        assert cleaned == 1

    async def test_cleanup_old_snapshots_cleans_corrupted_files(self, service: SnapshotService) -> None:
        service._ensure_dir()
        bad_path = Path(service._storage_dir) / "corrupted.json"
        bad_path.write_text("{invalid json", encoding="utf-8")

        cleaned = await service.cleanup_old_snapshots(days=7)
        assert cleaned == 1
        assert not bad_path.exists()

    # --- save_full_snapshot ---

    async def test_save_full_snapshot(self, service: SnapshotService) -> None:
        snapshot = ExecutionSnapshot(
            task_id="task-full",
            status="running",
            session_id="sess-full",
            completed_steps=["step-1"],
            remaining_steps=["step-2"],
            intermediate_results={"step-1": {"ok": True}},
            resource_state={"sandbox_id": "sbx-full"},
            checkpoint={"current": "step-2"},
        )
        await service.save_full_snapshot(snapshot)

        loaded = await service.load("task-full")
        assert loaded is not None
        assert loaded.session_id == "sess-full"
        assert loaded.completed_steps == ["step-1"]
        assert loaded.remaining_steps == ["step-2"]
        assert loaded.intermediate_results == {"step-1": {"ok": True}}
        assert loaded.resource_state == {"sandbox_id": "sbx-full"}
        assert loaded.status == "running"

    # --- Redis integration ---

    async def test_redis_save_and_load(self, service_with_mock_redis: SnapshotService) -> None:
        mock_redis = service_with_mock_redis._redis

        snapshot_data: dict[str, str] = {}
        for k, v in {
            "task_id": '"task-redis"',
            "session_id": '"sess-redis"',
            "status": '"running"',
            "progress": "0.5",
            "checkpoint": "{}",
            "completed_steps": '["step-1"]',
            "remaining_steps": '["step-2"]',
            "intermediate_results": '{"step-1": {"ok": true}}',
            "resource_state": '{"container_id": "docker-redis"}',
            "created_at": '"2025-01-01T00:00:00+00:00"',
            "updated_at": '"2025-01-01T00:00:05+00:00"',
        }.items():
            snapshot_data[k] = v

        mock_redis.hgetall = MagicMock(return_value=snapshot_data)

        loaded = await service_with_mock_redis.load("task-redis")
        assert loaded is not None
        assert loaded.task_id == "task-redis"
        assert loaded.session_id == "sess-redis"
        assert loaded.completed_steps == ["step-1"]
        assert loaded.remaining_steps == ["step-2"]

    async def test_redis_disabled_falls_back_to_file(self, service: SnapshotService) -> None:
        assert service._redis is None

        await service.save("task-no-redis", "running", {"step": 1})

        loaded = await service.load("task-no-redis")
        assert loaded is not None
        assert loaded.task_id == "task-no-redis"

    async def test_redis_failure_graceful_degradation(self, tmp_path: Path) -> None:
        mock_redis = MagicMock()
        mock_redis.hset = MagicMock(side_effect=ConnectionError("Redis down"))
        mock_redis.xadd = MagicMock(side_effect=ConnectionError("Redis down"))

        service = SnapshotService(storage_dir=str(tmp_path), redis_client=mock_redis)

        await service.save("task-redis-down", "running", {"step": 1})

        loaded = await service.load("task-redis-down")
        assert loaded is not None
        assert loaded.task_id == "task-redis-down"

    # --- Backward compatibility ---

    async def test_mvp_save_still_works(self, service: SnapshotService) -> None:
        await service.save(
            task_id="task-mvp",
            status="running",
            checkpoint={"step": 3, "url": "http://example.com"},
        )

        loaded = await service.load("task-mvp")
        assert loaded is not None
        assert loaded.task_id == "task-mvp"
        assert loaded.status == "running"
        assert loaded.progress == 0.3
        assert loaded.checkpoint == {"step": 3, "url": "http://example.com"}

    async def test_mvp_list_incomplete_still_works(self, service: SnapshotService) -> None:
        await service.save("task-running", "running", {})
        await service.save("task-passed", "passed", {})
        await service.save("task-retrying", "retrying", {})

        incomplete = await service.list_incomplete()
        task_ids = [s.task_id for s in incomplete]

        assert "task-running" in task_ids
        assert "task-retrying" in task_ids
        assert "task-passed" not in task_ids

    async def test_mvp_resume_still_works(self, service: SnapshotService) -> None:
        await service.save("task-resume-mvp", "running", {"step": 5})

        result = await service.resume("task-resume-mvp")
        assert result is not None
        assert result.task_id == "task-resume-mvp"
        assert result.checkpoint == {"step": 5}

    async def test_load_old_format_snapshot(self, service: SnapshotService) -> None:
        import json

        service._ensure_dir()
        old_data = {
            "task_id": "task-old-format",
            "status": "running",
            "progress": 0.3,
            "checkpoint": {"legacy": True},
            "created_at": datetime.now(UTC).isoformat(),
        }
        path = service._snapshot_path("task-old-format")
        path.write_text(json.dumps(old_data), encoding="utf-8")

        loaded = await service.load("task-old-format")
        assert loaded is not None
        assert loaded.task_id == "task-old-format"
        assert loaded.session_id == ""
        assert loaded.completed_steps == []
        assert loaded.remaining_steps == []
        assert loaded.intermediate_results == {}
        assert loaded.resource_state == {}


# ====================================================================
# TestExecutionSnapshotModel — SQLAlchemy 模型
# ====================================================================


class TestExecutionSnapshotModel:
    """ExecutionSnapshotModel — 数据库模型验证"""

    def test_model_fields(self) -> None:
        from testagent.models.snapshot import ExecutionSnapshotModel

        assert ExecutionSnapshotModel.__tablename__ == "execution_snapshots"

        columns = {c.name for c in ExecutionSnapshotModel.__table__.columns}
        expected = {
            "id",
            "created_at",
            "task_id",
            "session_id",
            "status",
            "progress",
            "checkpoint",
            "completed_steps",
            "remaining_steps",
            "intermediate_results",
            "resource_state",
            "error_detail",
            "updated_at",
        }
        assert columns == expected

    def test_snapshot_statuses_constant(self) -> None:
        from testagent.models.snapshot import SNAPSHOT_STATUSES

        assert "queued" in SNAPSHOT_STATUSES
        assert "running" in SNAPSHOT_STATUSES
        assert "retrying" in SNAPSHOT_STATUSES
        assert "passed" in SNAPSHOT_STATUSES
        assert "failed" in SNAPSHOT_STATUSES


# ====================================================================
# TestBreakpointResume — 断点续跑端到端模拟
# ====================================================================


class TestBreakpointResume:
    """断点续跑端到端模拟测试"""

    @pytest.fixture()
    def service(self, tmp_path: Path) -> SnapshotService:
        return SnapshotService(storage_dir=str(tmp_path))

    async def test_full_breakpoint_resume_workflow(self, service: SnapshotService) -> None:
        snapshot = ExecutionSnapshot(
            task_id="task-workflow",
            status="running",
            session_id="sess-workflow",
            completed_steps=[],
            remaining_steps=["step-1", "step-2", "step-3", "step-4"],
            resource_state={"sandbox_id": "sbx-workflow"},
            checkpoint={"total_steps": 4},
        )
        await service.save_full_snapshot(snapshot)

        await service.save_step_completion("task-workflow", "step-1", {"status": "passed", "time": 100})
        await service.save_step_completion("task-workflow", "step-2", {"status": "passed", "time": 200})

        context = await service.resume_from_snapshot("task-workflow")
        assert context["resume_from_step"] == "step-3"
        assert context["completed_steps"] == ["step-1", "step-2"]
        assert context["remaining_steps"] == ["step-3", "step-4"]

        await service.save_step_completion("task-workflow", "step-3", {"status": "passed", "time": 150})
        await service.save_step_completion("task-workflow", "step-4", {"status": "passed", "time": 180})

        loaded = await service.load("task-workflow")
        assert loaded is not None
        assert loaded.progress == 1.0
        assert len(loaded.completed_steps) == 4
        assert len(loaded.remaining_steps) == 0

    async def test_resume_after_interruption(self, service: SnapshotService) -> None:
        snapshot = ExecutionSnapshot(
            task_id="task-interrupt",
            status="running",
            session_id="sess-interrupt",
            completed_steps=["step-1"],
            remaining_steps=["step-2", "step-3"],
            intermediate_results={"step-1": {"status": "passed"}},
            resource_state={"sandbox_id": "sbx-interrupt", "container_id": "ctr-interrupt"},
            checkpoint={"current": "step-2"},
        )
        await service.save_full_snapshot(snapshot)

        context = await service.resume_from_snapshot("task-interrupt")

        assert context["resume_from_step"] == "step-2"
        assert context["resource_state"]["sandbox_id"] == "sbx-interrupt"
        assert context["intermediate_results"]["step-1"] == {"status": "passed"}

    async def test_cross_restart_recovery(self, service: SnapshotService) -> None:
        snapshot = ExecutionSnapshot(
            task_id="task-restart",
            status="running",
            session_id="sess-restart",
            completed_steps=["step-1", "step-2"],
            remaining_steps=["step-3"],
            intermediate_results={
                "step-1": {"status": "passed", "duration": 100},
                "step-2": {"status": "passed", "duration": 200},
            },
            resource_state={"sandbox_id": "sbx-restart"},
            checkpoint={},
        )
        await service.save_full_snapshot(snapshot)

        new_service = SnapshotService(storage_dir=str(service._storage_dir))

        context = await new_service.resume_from_snapshot("task-restart")
        assert context["resume_from_step"] == "step-3"
        assert context["intermediate_results"]["step-1"]["duration"] == 100

        loaded = await new_service.load("task-restart")
        assert loaded is not None
        assert loaded.completed_steps == ["step-1", "step-2"]
        assert loaded.remaining_steps == ["step-3"]

    async def test_breakpoint_resume_100_percent_success_rate(self, service: SnapshotService) -> None:
        num_tasks = 10
        for i in range(num_tasks):
            snapshot = ExecutionSnapshot(
                task_id=f"task-rate-{i}",
                status="running",
                session_id=f"sess-rate-{i}",
                completed_steps=[f"step-{j}" for j in range(i % 3)],
                remaining_steps=[f"step-{j}" for j in range(i % 3, 5)],
                intermediate_results={f"step-{j}": {"ok": True} for j in range(i % 3)},
                resource_state={"sandbox_id": f"sbx-{i}"},
                checkpoint={},
            )
            await service.save_full_snapshot(snapshot)

        success_count = 0
        for i in range(num_tasks):
            try:
                context = await service.resume_from_snapshot(f"task-rate-{i}")
                if context.get("resume_from_step") is not None:
                    success_count += 1
            except SnapshotNotFoundError:
                pass

        assert success_count == num_tasks
