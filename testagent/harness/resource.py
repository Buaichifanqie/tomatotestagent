from __future__ import annotations

import asyncio
import contextlib
import shutil
from typing import ClassVar

from testagent.common.logging import get_logger

logger = get_logger(__name__)


class ResourceManager:
    """Resource quota management and automatic cleanup.

    Responsible for:
    - Monitoring disk usage on the Docker filesystem.
    - Periodically scanning and cleaning exited containers and dangling
      images (every 10 minutes).
    - Pausing new task creation when disk usage exceeds thresholds.
    - Emergency cleanup when disk usage exceeds 90%.
    """

    CHECK_INTERVAL_SECONDS: ClassVar[int] = 600  # 10 minutes
    PAUSE_THRESHOLD: ClassVar[float] = 0.80
    EMERGENCY_THRESHOLD: ClassVar[float] = 0.90

    def __init__(self, docker_data_path: str = "/var/lib/docker") -> None:
        self._docker_data_path = docker_data_path
        self._paused: bool = False
        self._cleanup_task: asyncio.Task[None] | None = None

    async def start_periodic_cleanup(self) -> None:
        """Start the background periodic cleanup loop (10-minute interval)."""
        if self._cleanup_task is not None and not self._cleanup_task.done():
            logger.debug("Periodic cleanup already running")
            return

        async def _loop() -> None:
            while True:
                try:
                    await asyncio.sleep(self.CHECK_INTERVAL_SECONDS)
                    cleaned = await self.cleanup_exited_containers()
                    dangling = await self.cleanup_dangling_images()
                    disk_pct = await self.check_disk_usage()
                    logger.info(
                        "Periodic cleanup complete",
                        extra={
                            "containers_removed": cleaned,
                            "dangling_images_removed": dangling,
                            "disk_usage_pct": round(disk_pct * 100, 1),
                        },
                    )
                    await self._evaluate_thresholds(disk_pct)
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.exception("Error in periodic cleanup loop")

        self._cleanup_task = asyncio.create_task(_loop())
        logger.info("Periodic cleanup started", extra={"interval_seconds": self.CHECK_INTERVAL_SECONDS})

    async def stop_periodic_cleanup(self) -> None:
        if self._cleanup_task is not None and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None
            logger.info("Periodic cleanup stopped")

    async def check_disk_usage(self) -> float:
        """Check disk usage of the Docker data path.

        Returns:
            Fraction of used space (0.0 - 1.0).  Returns 0.0 if the
            path does not exist or cannot be read.
        """
        try:
            usage = shutil.disk_usage(self._docker_data_path)
            return usage.used / usage.total
        except FileNotFoundError:
            logger.warning(
                "Docker data path not found, cannot check disk usage",
                extra={"path": self._docker_data_path},
            )
            return 0.0
        except PermissionError:
            logger.warning(
                "Permission denied reading disk usage",
                extra={"path": self._docker_data_path},
            )
            return 0.0

    async def cleanup_exited_containers(self) -> int:
        """Remove all exited containers.

        Returns:
            Number of containers removed.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "container",
                "prune",
                "--force",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await proc.communicate()
            output = stdout.decode(errors="replace")

            lines = [ln for ln in output.splitlines() if ln.strip() and not ln.startswith("Total")]
            count = len(lines)
            if count > 0:
                logger.info("Removed exited containers", extra={"count": count})
            return count
        except Exception:
            logger.exception("Failed to prune exited containers")
            return 0

    async def cleanup_dangling_images(self) -> int:
        """Remove all dangling (untagged) Docker images.

        Returns:
            Number of images removed.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "image",
                "prune",
                "--force",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _stderr = await proc.communicate()
            output = stdout.decode(errors="replace")

            lines = [ln for ln in output.splitlines() if ln.strip() and not ln.startswith("Total")]
            count = len(lines)
            if count > 0:
                logger.info("Removed dangling images", extra={"count": count})
            return count
        except Exception:
            logger.exception("Failed to prune dangling images")
            return 0

    async def should_pause_new_tasks(self) -> bool:
        """Check whether new task creation should be paused.

        Returns *True* if disk usage exceeds the pause threshold (80%).
        """
        disk_pct = await self.check_disk_usage()
        should_pause = disk_pct >= self.PAUSE_THRESHOLD
        if should_pause and not self._paused:
            logger.warning(
                "Disk usage above pause threshold -- pausing new tasks",
                extra={"disk_usage_pct": round(disk_pct * 100, 1), "threshold": "80%"},
            )
            self._paused = True
        elif not should_pause and self._paused:
            logger.info("Disk usage below pause threshold -- resuming new tasks")
            self._paused = False
        return should_pause

    async def emergency_cleanup(self) -> None:
        """Emergency cleanup -- runs when disk usage exceeds 90%.

        Force removes **all** non-active containers (stopped, exited,
        created) and all dangling images.  This is more aggressive than
        the periodic cleanup.
        """
        disk_pct = await self.check_disk_usage()
        if disk_pct < self.EMERGENCY_THRESHOLD:
            logger.debug("Emergency cleanup not needed", extra={"disk_usage_pct": round(disk_pct * 100, 1)})
            return

        logger.warning(
            "Emergency cleanup triggered",
            extra={"disk_usage_pct": round(disk_pct * 100, 1), "threshold": "90%"},
        )

        # Remove all non-running containers (exited, created, dead)
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "container",
                "prune",
                "--force",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except Exception:
            logger.exception("Emergency container prune failed")

        # Aggressive image cleanup -- remove all dangling + unused
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "image",
                "prune",
                "--force",
                "--all",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except Exception:
            logger.exception("Emergency image prune failed")

        # Clean up build cache
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "builder",
                "prune",
                "--force",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except Exception:
            logger.exception("Emergency builder cache prune failed")

        logger.info("Emergency cleanup completed")

    async def _evaluate_thresholds(self, disk_pct: float) -> None:
        """Check thresholds and trigger actions if needed."""
        if disk_pct >= self.EMERGENCY_THRESHOLD:
            await self.emergency_cleanup()
        elif disk_pct >= self.PAUSE_THRESHOLD:
            self._paused = True
            logger.warning(
                "Disk usage above 80% -- new tasks will be paused",
                extra={"disk_usage_pct": round(disk_pct * 100, 1)},
            )
