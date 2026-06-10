"""Checkpoint manager for test plan execution persistence.

Saves execution progress after each TC completes so that interrupted plans
can be resumed from the last checkpoint. Uses atomic writes (write-to-temp
then os.replace) to prevent corruption from mid-write crashes.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from testagent.common import get_logger

if TYPE_CHECKING:
    from testagent.plan.models import PlanConfig, TestCase

from testagent.plan.models import ExecutionStatus, TCExecution

_logger = get_logger(__name__)

CHECKPOINT_FILENAME = "checkpoint.json"

# Terminal statuses — TCs that finished (pass or fail) and should NOT be re-run
_TERMINAL_STATUSES = frozenset({
    ExecutionStatus.EXECUTED,
    ExecutionStatus.FAILED,
    ExecutionStatus.BLOCKED,
})

# Interruptible statuses — TCs that were mid-flight when execution stopped
_INTERRUPTIBLE_STATUSES = frozenset({
    ExecutionStatus.PENDING,
    ExecutionStatus.RUNNING,
    ExecutionStatus.ABORTED,
})


# ── Exceptions ────────────────────────────────────────────────────────────


class CheckpointError(Exception):
    """Base exception for checkpoint operations."""


class CheckpointNotFoundError(CheckpointError):
    """No checkpoint file found."""


class CheckpointCorruptedError(CheckpointError):
    """Checkpoint file exists but is malformed."""


# ── Data ──────────────────────────────────────────────────────────────────


@dataclass
class CheckpointData:
    """Serializable checkpoint state."""

    plan_name: str
    created_at: str
    updated_at: str
    config_snapshot: dict[str, object]
    test_cases: list[dict[str, object]]
    completed_ids: list[str] = field(default_factory=list)
    aborted_ids: list[str] = field(default_factory=list)
    total_count: int = 0
    completed_count: int = 0


# ── Manager ───────────────────────────────────────────────────────────────


class CheckpointManager:
    """Manages checkpoint persistence for a test plan execution."""

    def __init__(self, output_dir: str | Path) -> None:
        self._path = Path(output_dir) / CHECKPOINT_FILENAME

    def exists(self) -> bool:
        """Check if a checkpoint file exists."""
        return self._path.is_file()

    @property
    def path(self) -> Path:
        return self._path

    def save(
        self,
        plan_name: str,
        config: PlanConfig,
        test_cases: list[TestCase],
    ) -> None:
        """Atomically write checkpoint to disk.

        Writes to a temporary file first, then does an atomic rename via
        ``os.replace()``.  If the process is killed mid-write, the original
        checkpoint file is left intact.
        """
        now = datetime.now().isoformat(timespec="seconds")

        completed_ids = []
        aborted_ids = []
        for tc in test_cases:
            status = tc.execution.status
            if status in _TERMINAL_STATUSES:
                completed_ids.append(tc.id)
            elif status in _INTERRUPTIBLE_STATUSES:
                aborted_ids.append(tc.id)

        data = CheckpointData(
            plan_name=plan_name,
            created_at=now,
            updated_at=now,
            config_snapshot=config.model_dump(),
            test_cases=[tc.model_dump() for tc in test_cases],
            completed_ids=completed_ids,
            aborted_ids=aborted_ids,
            total_count=len(test_cases),
            completed_count=len(completed_ids),
        )

        # Atomic write: temp file → replace
        tmp_fd, tmp_path = tempfile.mkstemp(
            suffix=".tmp",
            dir=self._path.parent,
            prefix=".checkpoint_",
        )
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(
                    _dataclass_to_dict(data),
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(tmp_path, self._path)
            _logger.debug(
                "Checkpoint saved",
                extra={
                    "extra_data": {
                        "plan": plan_name,
                        "completed": len(completed_ids),
                        "total": len(test_cases),
                    }
                },
            )
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def load(self) -> CheckpointData:
        """Load checkpoint from disk.

        Raises:
            CheckpointNotFoundError: if no checkpoint file exists.
            CheckpointCorruptedError: if the file is malformed.
        """
        if not self._path.is_file():
            raise CheckpointNotFoundError(
                f"Checkpoint file not found: {self._path}"
            )

        try:
            text = self._path.read_text(encoding="utf-8")
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CheckpointCorruptedError(
                f"Checkpoint file contains invalid JSON: {exc}"
            ) from exc
        except OSError as exc:
            raise CheckpointCorruptedError(
                f"Cannot read checkpoint file: {exc}"
            ) from exc

        try:
            return CheckpointData(
                plan_name=str(raw["plan_name"]),
                created_at=str(raw["created_at"]),
                updated_at=str(raw["updated_at"]),
                config_snapshot=dict(raw["config_snapshot"]),
                test_cases=list(raw["test_cases"]),
                completed_ids=list(raw.get("completed_ids", [])),
                aborted_ids=list(raw.get("aborted_ids", [])),
                total_count=int(raw.get("total_count", 0)),
                completed_count=int(raw.get("completed_count", 0)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CheckpointCorruptedError(
                f"Checkpoint file has unexpected structure: {exc}"
            ) from exc

    def load_and_resume(
        self,
    ) -> tuple[list[TestCase], list[TestCase]]:
        """Load checkpoint and split into (completed, remaining) TC lists.

        Completed TCs keep their full execution results.
        Remaining TCs (PENDING/RUNNING/ABORTED) are reset to PENDING
        with cleared execution state so they re-run from scratch.

        Returns:
            (completed_tcs, remaining_tcs)
        """
        from testagent.plan.models import TestCase

        data = self.load()

        completed: list[TestCase] = []
        remaining: list[TestCase] = []

        for tc_dict in data.test_cases:
            tc = TestCase(**tc_dict)
            if tc.execution.status in _TERMINAL_STATUSES:
                completed.append(tc)
            else:
                # Reset to PENDING — will re-run from scratch
                tc.execution = TCExecution()
                remaining.append(tc)

        return completed, remaining

    def delete(self) -> None:
        """Remove the checkpoint file."""
        try:
            self._path.unlink(missing_ok=True)
            _logger.debug("Checkpoint deleted", extra={"extra_data": {"path": str(self._path)}})
        except OSError as exc:
            _logger.warning(
                "Failed to delete checkpoint",
                extra={"extra_data": {"path": str(self._path), "error": str(exc)}},
            )


def _dataclass_to_dict(obj: object) -> object:
    """Recursively convert dataclass instances to dicts for JSON serialization."""
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _dataclass_to_dict(v) for k, v in obj.__dict__.items()}
    if isinstance(obj, list):
        return [_dataclass_to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    return obj
