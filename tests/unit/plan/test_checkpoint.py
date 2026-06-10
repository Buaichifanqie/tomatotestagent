"""Tests for testagent.plan.checkpoint module."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from testagent.plan.checkpoint import (
    CheckpointCorruptedError,
    CheckpointData,
    CheckpointManager,
    CheckpointNotFoundError,
)
from testagent.plan.models import (
    ExecutionStatus,
    PlanConfig,
    TCExecution,
    TestCase,
)


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_tc(
    tc_id: str = "TC-001",
    title: str = "Test",
    priority: str = "P1",
    status: ExecutionStatus = ExecutionStatus.PENDING,
) -> TestCase:
    tc = TestCase(id=tc_id, title=title, priority=priority)
    tc.execution.status = status
    return tc


# ── CheckpointManager ──────────────────────────────────────────────────────


class TestCheckpointManager:
    def test_exists_returns_false_when_no_file(self, tmp_path: Path) -> None:
        mgr = CheckpointManager(tmp_path)
        assert mgr.exists() is False

    def test_save_creates_file(self, tmp_path: Path) -> None:
        mgr = CheckpointManager(tmp_path)
        config = PlanConfig()
        tcs = [_make_tc()]
        mgr.save("test-plan", config, tcs)
        assert mgr.exists()
        assert (tmp_path / "checkpoint.json").is_file()

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        mgr = CheckpointManager(tmp_path)
        config = PlanConfig()
        tc1 = _make_tc("TC-001", "First", status=ExecutionStatus.EXECUTED)
        tc2 = _make_tc("TC-002", "Second", status=ExecutionStatus.FAILED)
        tc3 = _make_tc("TC-003", "Third", status=ExecutionStatus.PENDING)
        tc4 = _make_tc("TC-004", "Fourth", status=ExecutionStatus.RUNNING)

        mgr.save("my-plan", config, [tc1, tc2, tc3, tc4])

        data = mgr.load()
        assert data.plan_name == "my-plan"
        assert data.total_count == 4
        assert data.completed_count == 2
        assert data.completed_ids == ["TC-001", "TC-002"]
        assert data.aborted_ids == ["TC-003", "TC-004"]
        assert len(data.test_cases) == 4

    def test_load_and_resume_splits_correctly(self, tmp_path: Path) -> None:
        mgr = CheckpointManager(tmp_path)
        config = PlanConfig()
        tc1 = _make_tc("TC-001", status=ExecutionStatus.EXECUTED)
        tc2 = _make_tc("TC-002", status=ExecutionStatus.FAILED)
        tc3 = _make_tc("TC-003", status=ExecutionStatus.BLOCKED)
        tc4 = _make_tc("TC-004", status=ExecutionStatus.PENDING)
        tc5 = _make_tc("TC-005", status=ExecutionStatus.ABORTED)

        mgr.save("plan", config, [tc1, tc2, tc3, tc4, tc5])
        completed, remaining = mgr.load_and_resume()

        assert len(completed) == 3
        assert len(remaining) == 2
        assert [tc.id for tc in completed] == ["TC-001", "TC-002", "TC-003"]
        assert [tc.id for tc in remaining] == ["TC-004", "TC-005"]

    def test_load_and_resume_resets_running_tc(self, tmp_path: Path) -> None:
        mgr = CheckpointManager(tmp_path)
        config = PlanConfig()
        tc = _make_tc("TC-001", status=ExecutionStatus.RUNNING)

        mgr.save("plan", config, [tc])
        _, remaining = mgr.load_and_resume()

        assert len(remaining) == 1
        assert remaining[0].execution.status == ExecutionStatus.PENDING
        assert remaining[0].execution.error_message == ""

    def test_load_and_resume_preserves_completed_execution(self, tmp_path: Path) -> None:
        mgr = CheckpointManager(tmp_path)
        config = PlanConfig()
        tc = _make_tc("TC-001", status=ExecutionStatus.EXECUTED)
        tc.execution.duration_ms = 5000

        mgr.save("plan", config, [tc])
        completed, _ = mgr.load_and_resume()

        assert len(completed) == 1
        assert completed[0].execution.status == ExecutionStatus.EXECUTED
        assert completed[0].execution.duration_ms == 5000

    def test_delete_removes_file(self, tmp_path: Path) -> None:
        mgr = CheckpointManager(tmp_path)
        config = PlanConfig()
        mgr.save("plan", config, [_make_tc()])
        assert mgr.exists()

        mgr.delete()
        assert not mgr.exists()

    def test_delete_noop_when_no_file(self, tmp_path: Path) -> None:
        mgr = CheckpointManager(tmp_path)
        mgr.delete()  # should not raise

    def test_atomic_write_no_corruption(self, tmp_path: Path) -> None:
        """Verify temp file is cleaned up after successful save."""
        mgr = CheckpointManager(tmp_path)
        config = PlanConfig()
        mgr.save("plan", config, [_make_tc()])

        # No temp files should remain
        tmp_files = list(tmp_path.glob(".checkpoint_*"))
        assert len(tmp_files) == 0

    def test_checkpoint_json_is_valid(self, tmp_path: Path) -> None:
        """The checkpoint file should be valid JSON."""
        mgr = CheckpointManager(tmp_path)
        config = PlanConfig()
        mgr.save("plan", config, [_make_tc("TC-001"), _make_tc("TC-002")])

        raw = json.loads((tmp_path / "checkpoint.json").read_text(encoding="utf-8"))
        assert raw["plan_name"] == "plan"
        assert raw["total_count"] == 2
        assert isinstance(raw["test_cases"], list)
        assert len(raw["test_cases"]) == 2


# ── Error handling ─────────────────────────────────────────────────────────


class TestCheckpointErrors:
    def test_load_raises_not_found(self, tmp_path: Path) -> None:
        mgr = CheckpointManager(tmp_path)
        with pytest.raises(CheckpointNotFoundError):
            mgr.load()

    def test_load_raises_corrupted_on_invalid_json(self, tmp_path: Path) -> None:
        mgr = CheckpointManager(tmp_path)
        (tmp_path / "checkpoint.json").write_text("not json!!!", encoding="utf-8")
        with pytest.raises(CheckpointCorruptedError, match="invalid JSON"):
            mgr.load()

    def test_load_raises_corrupted_on_missing_fields(self, tmp_path: Path) -> None:
        mgr = CheckpointManager(tmp_path)
        (tmp_path / "checkpoint.json").write_text('{"foo": "bar"}', encoding="utf-8")
        with pytest.raises(CheckpointCorruptedError, match="unexpected structure"):
            mgr.load()

    def test_load_and_resume_raises_not_found(self, tmp_path: Path) -> None:
        mgr = CheckpointManager(tmp_path)
        with pytest.raises(CheckpointNotFoundError):
            mgr.load_and_resume()


# ── CheckpointData ─────────────────────────────────────────────────────────


class TestCheckpointData:
    def test_default_values(self) -> None:
        data = CheckpointData(
            plan_name="test",
            created_at="2026-01-01",
            updated_at="2026-01-01",
            config_snapshot={},
            test_cases=[],
        )
        assert data.completed_ids == []
        assert data.aborted_ids == []
        assert data.total_count == 0
        assert data.completed_count == 0
