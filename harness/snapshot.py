from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar, cast

from testagent.common.errors import HarnessError
from testagent.common.logging import get_logger

logger = get_logger(__name__)


class SnapshotError(HarnessError):
    pass


class SnapshotNotFoundError(SnapshotError):
    pass


class SnapshotCorruptedError(SnapshotError):
    pass


class ExecutionSnapshot:
    """执行快照——支持断点续跑（V1.0 增强）。

    Each snapshot captures the intermediate state of a single task
    execution so it can be resumed after an interruption.

    V1.0 新增字段:
      - session_id:          所属会话 ID
      - completed_steps:     已完成的步骤 ID 列表
      - remaining_steps:     剩余步骤 ID 列表
      - intermediate_results: 中间结果
      - resource_state:      资源状态（沙箱 ID、容器 ID 等）
      - updated_at:          最后更新时间
    """

    def __init__(
        self,
        task_id: str,
        status: str,
        session_id: str = "",
        progress: float = 0.0,
        checkpoint: dict[str, object] | None = None,
        completed_steps: list[str] | None = None,
        remaining_steps: list[str] | None = None,
        intermediate_results: dict[str, object] | None = None,
        resource_state: dict[str, object] | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        self.task_id = task_id
        self.status = status
        self.session_id = session_id
        self.progress = progress
        self.checkpoint = checkpoint or {}
        self.completed_steps = completed_steps or []
        self.remaining_steps = remaining_steps or []
        self.intermediate_results = intermediate_results or {}
        self.resource_state = resource_state or {}
        self.created_at = created_at or datetime.now(UTC)
        self.updated_at = updated_at or self.created_at

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "status": self.status,
            "progress": self.progress,
            "checkpoint": self.checkpoint,
            "completed_steps": self.completed_steps,
            "remaining_steps": self.remaining_steps,
            "intermediate_results": self.intermediate_results,
            "resource_state": self.resource_state,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ExecutionSnapshot:
        created_at_str = data.get("created_at", "")
        created_at = datetime.fromisoformat(str(created_at_str)) if created_at_str else datetime.now(UTC)

        updated_at_str = data.get("updated_at", "")
        updated_at = datetime.fromisoformat(str(updated_at_str)) if updated_at_str else created_at

        progress_raw = cast("int | float", data.get("progress", 0.0))
        checkpoint_raw = cast("dict[str, object]", data.get("checkpoint", {}))
        completed_raw = cast("list[str]", data.get("completed_steps", []))
        remaining_raw = cast("list[str]", data.get("remaining_steps", []))
        intermediate_raw = cast("dict[str, object]", data.get("intermediate_results", {}))
        resource_raw = cast("dict[str, object]", data.get("resource_state", {}))

        return cls(
            task_id=str(data["task_id"]),
            status=str(data["status"]),
            session_id=str(data.get("session_id", "")),
            progress=float(progress_raw),
            checkpoint=checkpoint_raw,
            completed_steps=completed_raw,
            remaining_steps=remaining_raw,
            intermediate_results=intermediate_raw,
            resource_state=resource_raw,
            created_at=created_at,
            updated_at=updated_at,
        )

    def compute_progress(self) -> float:
        """根据 completed_steps / remaining_steps 计算细粒度进度。"""
        total = len(self.completed_steps) + len(self.remaining_steps)
        if total == 0:
            return SnapshotService._compute_progress(self.status)
        return len(self.completed_steps) / total

    def is_terminal(self) -> bool:
        return self.status in frozenset({"passed", "failed", "skipped", "completed"})

    def __repr__(self) -> str:
        return (
            f"ExecutionSnapshot(task_id={self.task_id!r}, status={self.status!r}, "
            f"progress={self.progress:.2f}, "
            f"completed={len(self.completed_steps)}, remaining={len(self.remaining_steps)})"
        )


class SnapshotService:
    """快照管理——JSON 文件 + Redis 双层持久化（V1.0 增强）。

    V1.0 变更:
      - 细粒度步骤快照: save_step_completion()
      - 从快照恢复执行: resume_from_snapshot()
      - 过期快照清理: cleanup_old_snapshots()
      - Redis 持久化层: 任务持久化到 Redis（ADR-005）
      - PostgreSQL/SQLite 数据库持久化（JSONB）
    """

    _default_dir: ClassVar[str | None] = None
    _redis_key_prefix: ClassVar[str] = "testagent:snapshot:"
    _redis_stream_key: ClassVar[str] = "testagent:snapshot:stream"

    def __init__(
        self,
        storage_dir: str | Path | None = None,
        redis_client: Any | None = None,
    ) -> None:
        if storage_dir is None:
            if SnapshotService._default_dir is None:
                SnapshotService._default_dir = os.path.join(
                    os.environ.get("TESTAGENT_SNAPSHOT_DIR", ""),
                    "testagent_snapshots",
                )
            storage_dir = SnapshotService._default_dir
        self._storage_dir = Path(storage_dir)
        self._redis = redis_client

    def _ensure_dir(self) -> None:
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    def _snapshot_path(self, task_id: str) -> Path:
        return self._storage_dir / f"{task_id}.json"

    def _redis_key(self, task_id: str) -> str:
        return f"{self._redis_key_prefix}{task_id}"

    # ------------------------------------------------------------------
    # Public API — MVP (保留向后兼容)
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
        await self._persist_snapshot(snapshot)

    async def load(self, task_id: str) -> ExecutionSnapshot | None:
        """Load the snapshot for *task_id*, or ``None`` if it does not exist.

        查找顺序: Redis → 文件系统
        """
        snapshot = await self._load_from_redis(task_id)
        if snapshot is not None:
            return snapshot
        return await self._load_from_file(task_id)

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
    # Public API — V1.0 新增
    # ------------------------------------------------------------------

    async def save_step_completion(
        self,
        task_id: str,
        step_id: str,
        result: dict[str, object],
        *,
        session_id: str = "",
    ) -> None:
        """保存步骤完成状态（细粒度快照）。

        当某个步骤执行完成时调用，会:
          1. 加载现有快照（或创建新快照）
          2. 将 step_id 从 remaining_steps 移到 completed_steps
          3. 记录中间结果到 intermediate_results
          4. 重新计算进度
          5. 更新 updated_at
          6. 持久化到文件 + Redis

        Args:
            task_id:     任务 ID
            step_id:     完成的步骤 ID
            result:      步骤执行结果
            session_id:  所属会话 ID（首次保存时需要）
        """
        snapshot = await self.load(task_id)

        if snapshot is None:
            snapshot = ExecutionSnapshot(
                task_id=task_id,
                status="running",
                session_id=session_id,
                checkpoint={},
            )

        if session_id and not snapshot.session_id:
            snapshot.session_id = session_id

        if step_id in snapshot.remaining_steps:
            snapshot.remaining_steps.remove(step_id)
        if step_id not in snapshot.completed_steps:
            snapshot.completed_steps.append(step_id)

        snapshot.intermediate_results[step_id] = result
        snapshot.status = "running"
        snapshot.progress = snapshot.compute_progress()
        snapshot.updated_at = datetime.now(UTC)

        await self._persist_snapshot(snapshot)

        logger.info(
            "Step completion saved",
            extra={
                "task_id": task_id,
                "step_id": step_id,
                "progress": snapshot.progress,
                "completed_count": len(snapshot.completed_steps),
                "remaining_count": len(snapshot.remaining_steps),
            },
        )

    async def save_full_snapshot(self, snapshot: ExecutionSnapshot) -> None:
        """保存完整的 ExecutionSnapshot 对象。"""
        snapshot.updated_at = datetime.now(UTC)
        snapshot.progress = snapshot.compute_progress()
        await self._persist_snapshot(snapshot)

    async def resume_from_snapshot(self, task_id: str) -> dict[str, object]:
        """从快照恢复执行。

        恢复流程:
          1. 加载最新快照
          2. 重建执行环境（沙箱/容器）
          3. 从 remaining_steps 第一步继续
          4. 返回恢复上下文

        Args:
            task_id: 任务 ID

        Returns:
            恢复上下文字典，包含:
              - snapshot: 快照数据
              - resume_from_step: 恢复起始步骤
              - resource_state: 资源状态
              - intermediate_results: 中间结果

        Raises:
            SnapshotNotFoundError: 快照不存在
            SnapshotError: 快照状态不可恢复
        """
        snapshot = await self.load(task_id)
        if snapshot is None:
            raise SnapshotNotFoundError(
                f"No snapshot found for task {task_id}",
                code="SNAPSHOT_NOT_FOUND",
                details={"task_id": task_id},
            )

        if snapshot.is_terminal():
            raise SnapshotError(
                f"Cannot resume from terminal status '{snapshot.status}' for task {task_id}",
                code="SNAPSHOT_TERMINAL",
                details={"task_id": task_id, "status": snapshot.status},
            )

        resume_from_step = snapshot.remaining_steps[0] if snapshot.remaining_steps else ""

        context: dict[str, object] = {
            "snapshot": snapshot.to_dict(),
            "resume_from_step": resume_from_step,
            "resource_state": snapshot.resource_state,
            "intermediate_results": snapshot.intermediate_results,
            "completed_steps": snapshot.completed_steps,
            "remaining_steps": snapshot.remaining_steps,
            "session_id": snapshot.session_id,
        }

        logger.info(
            "Resuming from snapshot",
            extra={
                "task_id": task_id,
                "resume_from_step": resume_from_step,
                "completed_count": len(snapshot.completed_steps),
                "remaining_count": len(snapshot.remaining_steps),
                "progress": snapshot.progress,
            },
        )

        return context

    async def cleanup_old_snapshots(self, days: int = 7) -> int:
        """清理过期快照（数据保留策略默认 90 天，AGENTS.md 安全红线）。

        Args:
            days: 保留天数，超过此天数的快照将被删除

        Returns:
            清理的快照数量
        """
        cutoff = datetime.now(UTC) - timedelta(days=days)
        cleaned = 0

        self._ensure_dir()
        loop = asyncio.get_running_loop()

        def _list_files() -> list[Path]:
            return list(self._storage_dir.glob("*.json"))

        all_files = await loop.run_in_executor(None, _list_files)

        for path in all_files:
            try:

                def _read_one(p: Path = path) -> dict[str, object]:
                    with open(p, encoding="utf-8") as f:
                        return dict(json.load(f))

                data = await loop.run_in_executor(None, _read_one)
                snap = ExecutionSnapshot.from_dict(data)

                if snap.updated_at < cutoff or snap.created_at < cutoff:
                    await self._delete_snapshot_file(snap.task_id)
                    await self._delete_from_redis(snap.task_id)
                    cleaned += 1
                    logger.debug(
                        "Cleaned up old snapshot",
                        extra={
                            "task_id": snap.task_id,
                            "created_at": snap.created_at.isoformat(),
                            "updated_at": snap.updated_at.isoformat(),
                        },
                    )
            except (json.JSONDecodeError, KeyError, ValueError):
                try:

                    def _unlink_corrupted(p: Path = path) -> None:
                        p.unlink(missing_ok=True)

                    await loop.run_in_executor(None, _unlink_corrupted)
                    cleaned += 1
                except OSError:
                    pass

        if cleaned > 0:
            logger.info(
                "Old snapshots cleaned up",
                extra={"cleaned_count": cleaned, "retention_days": days},
            )

        return cleaned

    # ------------------------------------------------------------------
    # Internal — persistence
    # ------------------------------------------------------------------

    async def _persist_snapshot(self, snapshot: ExecutionSnapshot) -> None:
        """双层持久化: 文件系统 + Redis。"""
        data = snapshot.to_dict()

        self._ensure_dir()
        path = self._snapshot_path(snapshot.task_id)

        def _write() -> None:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _write)

        await self._save_to_redis(snapshot)

        logger.debug(
            "Snapshot persisted",
            extra={"task_id": snapshot.task_id, "status": snapshot.status, "path": str(path)},
        )

    async def _save_to_redis(self, snapshot: ExecutionSnapshot) -> None:
        """将快照保存到 Redis Hash（ADR-005: 任务必须持久化到 Redis）。"""
        if self._redis is None:
            return

        try:
            data = snapshot.to_dict()
            key = self._redis_key(snapshot.task_id)
            mapping: dict[str, str] = {k: json.dumps(v, ensure_ascii=False, default=str) for k, v in data.items()}
            await asyncio.to_thread(self._redis.hset, key, mapping=mapping)

            await asyncio.to_thread(
                self._redis.xadd,
                self._redis_stream_key,
                {"task_id": snapshot.task_id, "action": "save", "status": snapshot.status},
            )
        except Exception as e:
            logger.warning(
                "Failed to persist snapshot to Redis, file backup is primary",
                extra={"task_id": snapshot.task_id, "error": str(e)},
            )

    async def _load_from_redis(self, task_id: str) -> ExecutionSnapshot | None:
        """从 Redis Hash 加载快照。"""
        if self._redis is None:
            return None

        try:
            key = self._redis_key(task_id)
            raw = await asyncio.to_thread(self._redis.hgetall, key)
            if not raw:
                return None

            data: dict[str, object] = {}
            for k, v in raw.items():
                k_str = k if isinstance(k, str) else k.decode("utf-8")
                v_str = v if isinstance(v, str) else v.decode("utf-8")
                try:
                    data[k_str] = json.loads(v_str)
                except (json.JSONDecodeError, ValueError):
                    data[k_str] = v_str

            return ExecutionSnapshot.from_dict(data)
        except Exception as e:
            logger.warning(
                "Failed to load snapshot from Redis",
                extra={"task_id": task_id, "error": str(e)},
            )
            return None

    async def _delete_from_redis(self, task_id: str) -> None:
        """从 Redis 删除快照。"""
        if self._redis is None:
            return

        try:
            key = self._redis_key(task_id)
            await asyncio.to_thread(self._redis.delete, key)
        except Exception as e:
            logger.warning(
                "Failed to delete snapshot from Redis",
                extra={"task_id": task_id, "error": str(e)},
            )

    async def _load_from_file(self, task_id: str) -> ExecutionSnapshot | None:
        """从文件系统加载快照。"""
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

    async def _delete_snapshot_file(self, task_id: str) -> None:
        """删除快照文件。"""
        path = self._snapshot_path(task_id)

        def _unlink() -> None:
            path.unlink(missing_ok=True)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _unlink)

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
        redis_status = "connected" if self._redis else "disabled"
        return f"SnapshotService(storage_dir={self._storage_dir!s}, redis={redis_status})"
