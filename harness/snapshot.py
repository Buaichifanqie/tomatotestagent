from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, cast

from testagent.common.errors import HarnessError
from testagent.common.logging import get_logger

logger = get_logger(__name__)


class SnapshotError(HarnessError):
    pass


class ExecutionSnapshot:
    """执行快照——支持断点续跑。

    Each snapshot captures the intermediate state of a single task
    execution so it can be resumed after an interruption.
    """

    def __init__(
        self,
        task_id: str,
        status: str,
        progress: float = 0.0,
        checkpoint: dict[str, object] | None = None,
        created_at: datetime | None = None,
    ) -> None:
        self.task_id = task_id
        self.status = status
        self.progress = progress
        self.checkpoint = checkpoint or {}
        self.created_at = created_at or datetime.now(UTC)

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "status": self.status,
            "progress": self.progress,
            "checkpoint": self.checkpoint,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ExecutionSnapshot:
        created_at_str = data.get("created_at", "")
        created_at = datetime.fromisoformat(str(created_at_str)) if created_at_str else datetime.now(UTC)

        progress_raw = cast("int | float", data.get("progress", 0.0))
        checkpoint_raw = cast("dict[str, object]", data.get("checkpoint", {}))

        return cls(
            task_id=str(data["task_id"]),
            status=str(data["status"]),
            progress=float(progress_raw),
            checkpoint=checkpoint_raw,
            created_at=created_at,
        )


class SnapshotService:
    """快照管理——基于 JSON 文件的持久化存储。

    Snapshots are stored as ``.json`` files under a configurable
    storage directory (default: ``testagent_snapshots/`` in CWD).
    """

    _default_dir: ClassVar[str | None] = None

    def __init__(self, storage_dir: str | Path | None = None) -> None:
        if storage_dir is None:
            if SnapshotService._default_dir is None:
                SnapshotService._default_dir = os.path.join(
                    os.environ.get("TESTAGENT_SNAPSHOT_DIR", ""),
                    "testagent_snapshots",
                )
            storage_dir = SnapshotService._default_dir
        self._storage_dir = Path(storage_dir)

    def _ensure_dir(self) -> None:
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    def _snapshot_path(self, task_id: str) -> Path:
        return self._storage_dir / f"{task_id}.json"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def save(self, task_id: str, status: str, checkpoint: dict[str, object]) -> None:
        """Persist a snapshot for *task_id*.

        Args:
            task_id:   Unique task identifier.
            status:    Current execution status (e.g. ``running``, ``retrying``).
            checkpoint: Arbitrary JSON-serialisable state dict.
        """
        snapshot = ExecutionSnapshot(
            task_id=task_id,
            status=status,
            progress=self._compute_progress(status),
            checkpoint=checkpoint,
        )
        self._ensure_dir()
        path = self._snapshot_path(task_id)

        def _write() -> None:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(snapshot.to_dict(), f, ensure_ascii=False, indent=2)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _write)
        logger.debug(
            "Snapshot saved",
            extra={"task_id": task_id, "status": status, "path": str(path)},
        )

    async def load(self, task_id: str) -> ExecutionSnapshot | None:
        """Load the snapshot for *task_id*, or ``None`` if it does not exist."""
        path = self._snapshot_path(task_id)
        if not path.exists():
            return None

        def _read() -> dict[str, object]:
            with open(path, encoding="utf-8") as f:
                return dict(json.load(f))

        loop = asyncio.get_running_loop()
        try:
            data = await loop.run_in_executor(None, _read)
            return ExecutionSnapshot.from_dict(data)
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(
                "Failed to load snapshot",
                extra={"task_id": task_id, "error": str(e)},
            )
            return None

    async def list_incomplete(self) -> list[ExecutionSnapshot]:
        """Return all snapshots whose status is *not* a terminal state.

        Terminal states: ``passed``, ``failed``, ``skipped``, ``completed``.
        """
        self._ensure_dir()
        terminal_statuses = frozenset({"passed", "failed", "skipped", "completed"})
        result: list[ExecutionSnapshot] = []

        def _list_files() -> list[Path]:
            return list(self._storage_dir.glob("*.json"))

        loop = asyncio.get_running_loop()
        all_files = await loop.run_in_executor(None, _list_files)

        for path in all_files:

            def _read_one(p: Path = path) -> dict[str, object]:
                with open(p, encoding="utf-8") as f:
                    return dict(json.load(f))

            try:
                data = await loop.run_in_executor(None, _read_one)
                snap = ExecutionSnapshot.from_dict(data)
                if snap.status not in terminal_statuses:
                    result.append(snap)
            except (json.JSONDecodeError, KeyError, ValueError):
                continue

        return result

    async def resume(self, task_id: str) -> ExecutionSnapshot | None:
        """Load the snapshot for *task_id* and log the resumption event.

        This method loads the saved checkpoint so the caller can
        continue execution from where it left off.

        Returns:
            The :class:`ExecutionSnapshot` if found, else ``None``.
        """
        snapshot = await self.load(task_id)
        if snapshot is None:
            logger.warning(
                "No snapshot found to resume",
                extra={"task_id": task_id},
            )
            return None

        logger.info(
            "Resuming task from snapshot",
            extra={
                "task_id": task_id,
                "status": snapshot.status,
                "progress": snapshot.progress,
                "checkpoint_keys": list(snapshot.checkpoint.keys()),
            },
        )
        return snapshot

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_progress(status: str) -> float:
        mapping: dict[str, float] = {
            "queued": 0.0,
            "running": 0.3,
            "retrying": 0.5,
            "passed": 1.0,
            "failed": 1.0,
            "skipped": 1.0,
            "completed": 1.0,
        }
        return mapping.get(status, 0.0)

    def __repr__(self) -> str:
        return f"SnapshotService(storage_dir={self._storage_dir!s})"
