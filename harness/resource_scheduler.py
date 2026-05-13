from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, ClassVar

from testagent.common.errors import HarnessError
from testagent.common.logging import get_logger
from testagent.harness.resource import ResourceManager
from testagent.harness.sandbox import RESOURCE_PROFILES

if TYPE_CHECKING:
    from testagent.config.settings import TestAgentSettings

logger = get_logger(__name__)


class ResourceSchedulerError(HarnessError):
    pass


class ResourceScheduler:
    """智能资源调度器

    V1.0 并发执行路数目标：10 路并行。

    职责：
    - 检查系统资源是否足以接受新任务（并行数、磁盘、内存）
    - 按任务类型分配资源配额（CPU / 内存 / 隔离级别）
    - 跟踪当前运行中的任务
    - 按优先级排序待调度任务
    """

    MAX_CONCURRENCY: ClassVar[int] = 10
    DISK_PAUSE_THRESHOLD: ClassVar[float] = 0.80
    DISK_EMERGENCY_THRESHOLD: ClassVar[float] = 0.90

    def __init__(self, settings: TestAgentSettings | None = None) -> None:
        self._settings = settings
        self._running_tasks: dict[str, dict[str, object]] = {}
        self._lock = asyncio.Lock()
        self._resource_manager = ResourceManager()

    async def can_accept_task(self, task_type: str) -> bool:
        """检查是否可接受新任务。

        检查项：
        1. 当前并行数 < MAX_CONCURRENCY
        2. 磁盘使用 < 80%（PAUSE_THRESHOLD）
        3. 任务类型在 RESOURCE_PROFILES 中

        返回 True 表示可以接受新任务。
        """
        if task_type not in RESOURCE_PROFILES:
            logger.warning(
                "Unknown task type, rejecting",
                extra={"task_type": task_type},
            )
            return False

        async with self._lock:
            current_concurrency = len(self._running_tasks)
            if current_concurrency >= self.MAX_CONCURRENCY:
                logger.warning(
                    "Concurrency limit reached, rejecting task",
                    extra={
                        "current": current_concurrency,
                        "max": self.MAX_CONCURRENCY,
                        "task_type": task_type,
                    },
                )
                return False

        disk_usage = await self._resource_manager.check_disk_usage()
        if disk_usage >= self.DISK_PAUSE_THRESHOLD:
            logger.warning(
                "Disk usage above pause threshold, rejecting task",
                extra={
                    "disk_usage_pct": round(disk_usage * 100, 1),
                    "threshold_pct": 80.0,
                    "task_type": task_type,
                },
            )
            return False

        return True

    async def allocate_resources(self, task_type: str) -> dict[str, object]:
        """根据任务类型分配资源配额。

        - api_test: 1CPU / 512MB / Docker
        - web_test: 2CPU / 2GB   / Docker
        - app_test: 4CPU / 4GB   / MicroVM（V1.0）

        返回资源配额字典。
        """
        if task_type not in RESOURCE_PROFILES:
            raise ResourceSchedulerError(
                f"Unknown task type: {task_type}",
                code="UNKNOWN_TASK_TYPE",
                details={"task_type": task_type},
            )

        profile = RESOURCE_PROFILES[task_type]

        isolation = "microvm" if task_type == "app_test" else "docker"

        return {
            "cpus": profile.cpus,
            "mem_limit": profile.mem_limit,
            "timeout": profile.timeout,
            "isolation_level": isolation,
            "read_only": profile.read_only,
        }

    async def register_task(
        self,
        task_id: str,
        task_type: str,
        resources: dict[str, object],
    ) -> None:
        """将任务注册为运行中状态。"""
        async with self._lock:
            if task_id in self._running_tasks:
                logger.warning(
                    "Task already registered as running",
                    extra={"task_id": task_id},
                )
                return
            self._running_tasks[task_id] = {
                "task_type": task_type,
                "resources": resources,
            }
            logger.info(
                "Task registered",
                extra={
                    "task_id": task_id,
                    "task_type": task_type,
                    "running_count": len(self._running_tasks),
                },
            )

    async def unregister_task(self, task_id: str) -> None:
        """将任务从运行中状态移除。"""
        async with self._lock:
            if task_id not in self._running_tasks:
                logger.warning(
                    "Task not found in running tasks",
                    extra={"task_id": task_id},
                )
                return
            del self._running_tasks[task_id]
            logger.info(
                "Task unregistered",
                extra={
                    "task_id": task_id,
                    "running_count": len(self._running_tasks),
                },
            )

    async def get_running_tasks(self) -> list[dict[str, object]]:
        """获取当前运行中的任务列表。"""
        async with self._lock:
            return [{"task_id": tid, **info} for tid, info in self._running_tasks.items()]

    async def get_resource_usage(self) -> dict[str, object]:
        """获取资源使用概览。

        返回：
            - running_tasks: 当前运行中任务数
            - max_concurrency: 最大并行数
            - concurrency_usage_pct: 并行使用率百分比
            - disk_usage_pct: 磁盘使用率百分比
            - paused: 是否暂停新任务
        """
        async with self._lock:
            running_count = len(self._running_tasks)

        disk_usage = await self._resource_manager.check_disk_usage()
        paused = disk_usage >= self.DISK_PAUSE_THRESHOLD

        return {
            "running_tasks": running_count,
            "max_concurrency": self.MAX_CONCURRENCY,
            "concurrency_usage_pct": round((running_count / self.MAX_CONCURRENCY) * 100, 1),
            "disk_usage_pct": round(disk_usage * 100, 1),
            "paused": paused,
        }

    async def check_disk_emergency(self) -> bool:
        """检查磁盘紧急状态。

        磁盘使用超 90% 阈值时执行紧急清理（所有非活跃容器）。
        返回 True 表示已触发紧急清理。
        """
        disk_usage = await self._resource_manager.check_disk_usage()
        if disk_usage >= self.DISK_EMERGENCY_THRESHOLD:
            logger.warning(
                "Disk usage above emergency threshold, triggering cleanup",
                extra={
                    "disk_usage_pct": round(disk_usage * 100, 1),
                    "threshold_pct": 90.0,
                },
            )
            await self._resource_manager.emergency_cleanup()
            return True
        return False

    def prioritize(
        self,
        tasks: list[dict[str, object]],
    ) -> list[dict[str, object]]:
        """按优先级排序任务队列。

        排序规则（从高到低）：
        1. 用户显式优先级（priority 字段，数值越高越优先）
        2. 依赖链前序（depends_on 为空的任务优先）
        3. 默认顺序（稳定排序保持原有相对顺序）
        """

        def _sort_key(t: dict[str, object]) -> tuple[int, int]:
            priority = t.get("priority", 0)
            if not isinstance(priority, int):
                priority = 0
            depends_on = t.get("depends_on")
            has_dependency = 1 if depends_on and str(depends_on).strip() else 0
            return (-priority, has_dependency)

        return sorted(tasks, key=_sort_key)
