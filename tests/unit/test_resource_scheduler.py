from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from testagent.common.errors import HarnessError
from testagent.harness.resource_scheduler import (
    ResourceScheduler,
    ResourceSchedulerError,
)
from testagent.harness.sandbox import RESOURCE_PROFILES

# ====================================================================
# Fixtures
# ====================================================================


@pytest.fixture()
def scheduler() -> ResourceScheduler:
    sched = ResourceScheduler()
    sched._running_tasks = {}
    return sched


# ====================================================================
# ResourceSchedulerError
# ====================================================================


class TestResourceSchedulerError:
    def test_is_harness_error(self) -> None:
        assert issubclass(ResourceSchedulerError, HarnessError)

    def test_default_code(self) -> None:
        err = ResourceSchedulerError("oops")
        assert err.code == "UNKNOWN"

    def test_custom_code_and_details(self) -> None:
        err = ResourceSchedulerError(
            "bad type",
            code="UNKNOWN_TASK_TYPE",
            details={"task_type": "invalid"},
        )
        assert err.code == "UNKNOWN_TASK_TYPE"
        assert err.details == {"task_type": "invalid"}


# ====================================================================
# ResourceScheduler — can_accept_task
# ====================================================================


class TestCanAcceptTask:
    async def test_accepts_valid_task_type_when_under_limit(self, scheduler: ResourceScheduler) -> None:
        scheduler._running_tasks = {}
        with patch.object(
            scheduler._resource_manager,
            "check_disk_usage",
            AsyncMock(return_value=0.5),
        ):
            result = await scheduler.can_accept_task("api_test")
            assert result is True

    async def test_rejects_unknown_task_type(self, scheduler: ResourceScheduler) -> None:
        result = await scheduler.can_accept_task("unknown_type")
        assert result is False

    async def test_rejects_when_concurrency_at_limit(self, scheduler: ResourceScheduler) -> None:
        scheduler._running_tasks = {
            f"task-{i}": {"task_type": "api_test", "resources": {}} for i in range(ResourceScheduler.MAX_CONCURRENCY)
        }
        with patch.object(
            scheduler._resource_manager,
            "check_disk_usage",
            AsyncMock(return_value=0.5),
        ):
            result = await scheduler.can_accept_task("api_test")
            assert result is False

    async def test_accepts_when_concurrency_one_below_limit(self, scheduler: ResourceScheduler) -> None:
        scheduler._running_tasks = {
            f"task-{i}": {"task_type": "api_test", "resources": {}}
            for i in range(ResourceScheduler.MAX_CONCURRENCY - 1)
        }
        with patch.object(
            scheduler._resource_manager,
            "check_disk_usage",
            AsyncMock(return_value=0.5),
        ):
            result = await scheduler.can_accept_task("api_test")
            assert result is True

    async def test_rejects_when_disk_above_threshold(self, scheduler: ResourceScheduler) -> None:
        scheduler._running_tasks = {}
        with patch.object(
            scheduler._resource_manager,
            "check_disk_usage",
            AsyncMock(return_value=0.85),
        ):
            result = await scheduler.can_accept_task("api_test")
            assert result is False

    async def test_accepts_when_disk_at_threshold_boundary(self, scheduler: ResourceScheduler) -> None:
        scheduler._running_tasks = {}
        with patch.object(
            scheduler._resource_manager,
            "check_disk_usage",
            AsyncMock(return_value=0.79),
        ):
            result = await scheduler.can_accept_task("api_test")
            assert result is True

    async def test_concurrency_check_happens_before_disk_check(self, scheduler: ResourceScheduler) -> None:
        scheduler._running_tasks = {
            f"task-{i}": {"task_type": "api_test", "resources": {}} for i in range(ResourceScheduler.MAX_CONCURRENCY)
        }
        mock_disk = AsyncMock(return_value=0.3)
        with patch.object(scheduler._resource_manager, "check_disk_usage", mock_disk):
            result = await scheduler.can_accept_task("api_test")
            assert result is False
        mock_disk.assert_not_called()


# ====================================================================
# ResourceScheduler — allocate_resources
# ====================================================================


class TestAllocateResources:
    async def test_api_test_allocates_1cpu_512mb_docker(self, scheduler: ResourceScheduler) -> None:
        resources = await scheduler.allocate_resources("api_test")
        assert resources["cpus"] == 1
        assert resources["mem_limit"] == "512m"
        assert resources["isolation_level"] == "docker"
        assert resources["read_only"] is True

    async def test_web_test_allocates_2cpu_2gb_docker(self, scheduler: ResourceScheduler) -> None:
        resources = await scheduler.allocate_resources("web_test")
        assert resources["cpus"] == 2
        assert resources["mem_limit"] == "2g"
        assert resources["isolation_level"] == "docker"
        assert resources["read_only"] is True

    async def test_app_test_allocates_4cpu_4gb_microvm(self, scheduler: ResourceScheduler) -> None:
        resources = await scheduler.allocate_resources("app_test")
        assert resources["cpus"] == 4
        assert resources["mem_limit"] == "4g"
        assert resources["isolation_level"] == "microvm"
        assert resources["read_only"] is True

    async def test_raises_for_unknown_task_type(self, scheduler: ResourceScheduler) -> None:
        with pytest.raises(ResourceSchedulerError, match="Unknown task type"):
            await scheduler.allocate_resources("nonexistent")

    async def test_all_profiles_covered(self, scheduler: ResourceScheduler) -> None:
        for task_type in RESOURCE_PROFILES:
            resources = await scheduler.allocate_resources(task_type)
            profile = RESOURCE_PROFILES[task_type]
            assert resources["cpus"] == profile.cpus
            assert resources["mem_limit"] == profile.mem_limit
            assert resources["timeout"] == profile.timeout
            assert resources["read_only"] == profile.read_only


# ====================================================================
# ResourceScheduler — register_task / unregister_task
# ====================================================================


class TestRegisterUnregisterTask:
    async def test_registers_task_and_updates_running_count(self, scheduler: ResourceScheduler) -> None:
        resources = {"cpus": 1, "mem_limit": "512m"}
        await scheduler.register_task("task-001", "api_test", resources)
        running = await scheduler.get_running_tasks()
        assert len(running) == 1
        assert running[0]["task_id"] == "task-001"

    async def test_unregister_removes_task(self, scheduler: ResourceScheduler) -> None:
        await scheduler.register_task("task-001", "api_test", {"cpus": 1})
        await scheduler.unregister_task("task-001")
        running = await scheduler.get_running_tasks()
        assert len(running) == 0

    async def test_register_duplicate_does_not_increase_count(self, scheduler: ResourceScheduler) -> None:
        resources: dict[str, object] = {"cpus": 1}
        await scheduler.register_task("task-001", "api_test", resources)
        await scheduler.register_task("task-001", "api_test", resources)
        running = await scheduler.get_running_tasks()
        assert len(running) == 1

    async def test_unregister_unknown_does_not_raise(self, scheduler: ResourceScheduler) -> None:
        await scheduler.unregister_task("nonexistent")
        running = await scheduler.get_running_tasks()
        assert len(running) == 0

    async def test_register_tasks_tracks_correct_count(self, scheduler: ResourceScheduler) -> None:
        resources: dict[str, object] = {"cpus": 1}
        for i in range(5):
            await scheduler.register_task(f"task-{i:03d}", "api_test", resources)
        running = await scheduler.get_running_tasks()
        assert len(running) == 5


# ====================================================================
# ResourceScheduler — get_resource_usage
# ====================================================================


class TestGetResourceUsage:
    async def test_reports_metrics_correctly(self, scheduler: ResourceScheduler) -> None:
        scheduler._running_tasks = {
            "task-001": {"task_type": "api_test", "resources": {}},
            "task-002": {"task_type": "web_test", "resources": {}},
        }
        with patch.object(
            scheduler._resource_manager,
            "check_disk_usage",
            AsyncMock(return_value=0.5),
        ):
            usage = await scheduler.get_resource_usage()

        assert usage["running_tasks"] == 2
        assert usage["max_concurrency"] == ResourceScheduler.MAX_CONCURRENCY
        assert usage["concurrency_usage_pct"] == 20.0
        assert usage["disk_usage_pct"] == 50.0
        assert usage["paused"] is False

    async def test_paused_true_when_disk_above_threshold(self, scheduler: ResourceScheduler) -> None:
        with patch.object(
            scheduler._resource_manager,
            "check_disk_usage",
            AsyncMock(return_value=0.85),
        ):
            usage = await scheduler.get_resource_usage()
            assert usage["paused"] is True

    async def test_reports_zero_running_when_empty(self, scheduler: ResourceScheduler) -> None:
        with patch.object(
            scheduler._resource_manager,
            "check_disk_usage",
            AsyncMock(return_value=0.3),
        ):
            usage = await scheduler.get_resource_usage()
        assert usage["running_tasks"] == 0
        assert usage["concurrency_usage_pct"] == 0.0


# ====================================================================
# ResourceScheduler — check_disk_emergency
# ====================================================================


class TestCheckDiskEmergency:
    async def test_does_not_trigger_below_threshold(self, scheduler: ResourceScheduler) -> None:
        with patch.object(
            scheduler._resource_manager,
            "check_disk_usage",
            AsyncMock(return_value=0.5),
        ):
            result = await scheduler.check_disk_emergency()
            assert result is False

    async def test_triggers_emergency_cleanup_at_90_percent(self, scheduler: ResourceScheduler) -> None:
        with (
            patch.object(
                scheduler._resource_manager,
                "check_disk_usage",
                AsyncMock(return_value=0.92),
            ),
            patch.object(
                scheduler._resource_manager,
                "emergency_cleanup",
                AsyncMock(),
            ) as mock_cleanup,
        ):
            result = await scheduler.check_disk_emergency()
            assert result is True
            mock_cleanup.assert_awaited_once()

    async def test_triggers_at_exact_90_percent(self, scheduler: ResourceScheduler) -> None:
        with (
            patch.object(
                scheduler._resource_manager,
                "check_disk_usage",
                AsyncMock(return_value=0.90),
            ),
            patch.object(
                scheduler._resource_manager,
                "emergency_cleanup",
                AsyncMock(),
            ) as mock_cleanup,
        ):
            result = await scheduler.check_disk_emergency()
            assert result is True
            mock_cleanup.assert_awaited_once()


# ====================================================================
# ResourceScheduler — prioritize
# ====================================================================


class TestPrioritize:
    def test_higher_priority_comes_first(self, scheduler: ResourceScheduler) -> None:
        tasks: list[dict[str, object]] = [
            {"task_id": "low", "priority": 1},
            {"task_id": "high", "priority": 10},
            {"task_id": "mid", "priority": 5},
        ]
        sorted_tasks = scheduler.prioritize(tasks)
        assert sorted_tasks[0]["task_id"] == "high"
        assert sorted_tasks[1]["task_id"] == "mid"
        assert sorted_tasks[2]["task_id"] == "low"

    def test_tasks_without_dependency_priority_above_dependent(self, scheduler: ResourceScheduler) -> None:
        tasks: list[dict[str, object]] = [
            {"task_id": "B", "priority": 5, "depends_on": "A"},
            {"task_id": "A", "priority": 5, "depends_on": ""},
        ]
        sorted_tasks = scheduler.prioritize(tasks)
        assert sorted_tasks[0]["task_id"] == "A"
        assert sorted_tasks[1]["task_id"] == "B"

    def test_same_priority_preserves_stable_order(self, scheduler: ResourceScheduler) -> None:
        tasks: list[dict[str, object]] = [
            {"task_id": "first", "priority": 5},
            {"task_id": "second", "priority": 5},
            {"task_id": "third", "priority": 5},
        ]
        sorted_tasks = scheduler.prioritize(tasks)
        assert sorted_tasks[0]["task_id"] == "first"
        assert sorted_tasks[1]["task_id"] == "second"
        assert sorted_tasks[2]["task_id"] == "third"

    def test_priority_takes_precedence_over_dependency(self, scheduler: ResourceScheduler) -> None:
        tasks: list[dict[str, object]] = [
            {"task_id": "dependent_high", "priority": 10, "depends_on": "X"},
            {"task_id": "independent_low", "priority": 1, "depends_on": None},
        ]
        sorted_tasks = scheduler.prioritize(tasks)
        assert sorted_tasks[0]["task_id"] == "dependent_high"
        assert sorted_tasks[1]["task_id"] == "independent_low"

    def test_empty_list_returns_empty(self, scheduler: ResourceScheduler) -> None:
        assert scheduler.prioritize([]) == []

    def test_single_task_returns_same(self, scheduler: ResourceScheduler) -> None:
        tasks: list[dict[str, object]] = [{"task_id": "only", "priority": 5}]
        assert scheduler.prioritize(tasks) == tasks

    def test_task_missing_priority_defaults_to_zero(self, scheduler: ResourceScheduler) -> None:
        tasks: list[dict[str, object]] = [
            {"task_id": "A", "priority": 5},
            {"task_id": "B"},
        ]
        sorted_tasks = scheduler.prioritize(tasks)
        assert sorted_tasks[0]["task_id"] == "A"
        assert sorted_tasks[1]["task_id"] == "B"


# ====================================================================
# ResourceScheduler — integration-style: register + can_accept
# ====================================================================


class TestSchedulerIntegration:
    async def test_full_scheduler_lifecycle(self, scheduler: ResourceScheduler) -> None:
        with patch.object(
            scheduler._resource_manager,
            "check_disk_usage",
            AsyncMock(return_value=0.4),
        ):
            assert await scheduler.can_accept_task("api_test") is True

            resources = await scheduler.allocate_resources("api_test")
            assert resources["cpus"] == 1

            await scheduler.register_task("task-001", "api_test", resources)
            running = await scheduler.get_running_tasks()
            assert len(running) == 1

            await scheduler.unregister_task("task-001")
            running = await scheduler.get_running_tasks()
            assert len(running) == 0

    async def test_scheduler_rejects_when_full(self, scheduler: ResourceScheduler) -> None:
        scheduler._running_tasks = {
            f"task-{i}": {"task_type": "api_test", "resources": {}} for i in range(ResourceScheduler.MAX_CONCURRENCY)
        }
        with patch.object(
            scheduler._resource_manager,
            "check_disk_usage",
            AsyncMock(return_value=0.4),
        ):
            assert await scheduler.can_accept_task("api_test") is False

    async def test_prioritize_and_accept_workflow(self, scheduler: ResourceScheduler) -> None:
        pending_tasks: list[dict[str, object]] = [
            {"task_id": "low_prio", "priority": 1, "task_type": "api_test"},
            {"task_id": "high_prio", "priority": 10, "task_type": "web_test"},
        ]
        sorted_tasks = scheduler.prioritize(pending_tasks)
        assert sorted_tasks[0]["task_id"] == "high_prio"

        with patch.object(
            scheduler._resource_manager,
            "check_disk_usage",
            AsyncMock(return_value=0.3),
        ):
            for t in sorted_tasks:
                task_type = str(t.get("task_type", "api_test"))
                assert await scheduler.can_accept_task(task_type) is True
