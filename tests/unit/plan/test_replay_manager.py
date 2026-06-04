from __future__ import annotations

import pytest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from testagent.plan.models import ExecutionStatus, TestCase, TestStep, TCExecution
from testagent.models.failed_replay import FailedCaseReplay


def _make_tc(tc_id: str, title: str, status: ExecutionStatus, error_msg: str | None = None, failed_step: int | None = None) -> TestCase:
    """Helper to create a TestCase with execution status."""
    tc = TestCase(id=tc_id, title=title, steps=[
        TestStep(step=1, action="tap", target="button", value=""),
    ])
    tc.execution = TCExecution(status=status)
    if error_msg:
        tc.execution.error_message = error_msg
    if failed_step is not None:
        tc.execution.failed_step = failed_step
    return tc


class TestCaptureFailures:
    """capture_failures upserts FAILs and auto-resolves PASSes."""

    @pytest.mark.asyncio
    async def test_captures_failed_cases(self):
        from testagent.plan.replay_manager import capture_failures

        mock_repo = AsyncMock()
        mock_repo.get_by_app_and_case_id = AsyncMock(return_value=None)
        mock_repo.create = AsyncMock(side_effect=lambda e: e)

        failed_tc = _make_tc("TC-001", "搜索测试", ExecutionStatus.FAILED, "Element not found", 2)
        passed_tc = _make_tc("TC-002", "登录测试", ExecutionStatus.EXECUTED)

        await capture_failures(
            executed_tcs=[failed_tc, passed_tc],
            run_id="run-001",
            app_id="tv.danmaku.bili",
            report_path="/reports/test.html",
            repository=mock_repo,
        )

        mock_repo.create.assert_called_once()
        created = mock_repo.create.call_args[0][0]
        assert created.app_id == "tv.danmaku.bili"
        assert created.test_case_id == "TC-001"
        assert created.original_error_message == "Element not found"
        assert created.original_failed_step == 2
        assert created.original_report_path == "/reports/test.html"

    @pytest.mark.asyncio
    async def test_skips_passed_cases_no_existing_record(self):
        from testagent.plan.replay_manager import capture_failures

        mock_repo = AsyncMock()
        mock_repo.get_by_app_and_case_id = AsyncMock(return_value=None)

        passed_tc = _make_tc("TC-002", "登录测试", ExecutionStatus.EXECUTED)

        await capture_failures(
            executed_tcs=[passed_tc],
            run_id="run-001",
            app_id="tv.danmaku.bili",
            report_path="/reports/test.html",
            repository=mock_repo,
        )

        mock_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_resolves_passed_cases_with_existing_record(self):
        from testagent.plan.replay_manager import capture_failures

        existing = FailedCaseReplay(
            app_id="tv.danmaku.bili",
            run_id="run-old",
            test_case_id="TC-002",
            test_case_name="登录测试",
            original_status="FAILED",
            test_case_data={},
            original_run_timestamp=datetime.now(UTC),
        )
        existing.id = "existing-id"

        mock_repo = AsyncMock()
        mock_repo.get_by_app_and_case_id = AsyncMock(return_value=existing)
        mock_repo.update = AsyncMock(return_value=existing)

        passed_tc = _make_tc("TC-002", "登录测试", ExecutionStatus.EXECUTED)

        await capture_failures(
            executed_tcs=[passed_tc],
            run_id="run-001",
            app_id="tv.danmaku.bili",
            report_path="/reports/test.html",
            repository=mock_repo,
        )

        mock_repo.update.assert_called_once()
        update_data = mock_repo.update.call_args[0][1]
        assert update_data["resolved"] == 1
        assert update_data["last_replay_status"] == "PASSED"
        assert update_data["resolved_at"] is not None

    @pytest.mark.asyncio
    async def test_builds_prerequisite_chain(self):
        from testagent.plan.replay_manager import capture_failures

        mock_repo = AsyncMock()
        mock_repo.get_by_app_and_case_id = AsyncMock(return_value=None)
        mock_repo.create = AsyncMock(side_effect=lambda e: e)

        tc1 = _make_tc("TC-001", "前置搜索", ExecutionStatus.EXECUTED)
        tc2 = _make_tc("TC-002", "前置登录", ExecutionStatus.EXECUTED)
        tc3 = _make_tc("TC-003", "目标测试", ExecutionStatus.FAILED, "fail", 1)

        await capture_failures(
            executed_tcs=[tc1, tc2, tc3],
            run_id="run-001",
            app_id="tv.danmaku.bili",
            report_path="/reports/test.html",
            repository=mock_repo,
        )

        created = mock_repo.create.call_args[0][0]
        assert created.prerequisite_case_ids == ["TC-001", "TC-002"]
        assert len(created.prerequisite_case_data) == 2

    @pytest.mark.asyncio
    async def test_captures_blocked_status(self):
        from testagent.plan.replay_manager import capture_failures

        mock_repo = AsyncMock()
        mock_repo.get_by_app_and_case_id = AsyncMock(return_value=None)
        mock_repo.create = AsyncMock(side_effect=lambda e: e)

        blocked_tc = _make_tc("TC-001", "被阻塞", ExecutionStatus.BLOCKED)

        await capture_failures(
            executed_tcs=[blocked_tc],
            run_id="run-001",
            app_id="tv.danmaku.bili",
            report_path="/reports/test.html",
            repository=mock_repo,
        )

        mock_repo.create.assert_called_once()
        created = mock_repo.create.call_args[0][0]
        assert created.original_status == "BLOCKED"


class TestGetPending:
    """get_pending delegates to repository."""

    @pytest.mark.asyncio
    async def test_returns_pending_records(self):
        from testagent.plan.replay_manager import get_pending

        mock_repo = AsyncMock()
        record = FailedCaseReplay(
            app_id="tv.danmaku.bili",
            run_id="run-001",
            test_case_id="TC-001",
            test_case_name="test",
            original_status="FAILED",
            test_case_data={},
            original_run_timestamp=datetime.now(UTC),
        )
        mock_repo.get_pending = AsyncMock(return_value=[record])

        results = await get_pending("tv.danmaku.bili", mock_repo)
        assert len(results) == 1
        mock_repo.get_pending.assert_called_once_with("tv.danmaku.bili", include_blocked=False)


class TestPrerequisiteDedup:
    """_merge_prerequisites deduplicates prerequisite chains."""

    def test_dedup_preserves_order(self):
        from testagent.plan.replay_manager import _merge_prerequisites

        prereqs = [
            (["TC-LOGIN", "TC-SEARCH"], [{"id": "TC-LOGIN"}, {"id": "TC-SEARCH"}]),
            (["TC-LOGIN", "TC-SEARCH", "TC-PLAY"], [{"id": "TC-LOGIN"}, {"id": "TC-SEARCH"}, {"id": "TC-PLAY"}]),
        ]
        target_ids = ["TC-A", "TC-B"]

        merged_ids, merged_data = _merge_prerequisites(prereqs, target_ids)

        assert merged_ids == ["TC-LOGIN", "TC-SEARCH", "TC-PLAY", "TC-A", "TC-B"]
        assert len(merged_data) == 3
        assert [d["id"] for d in merged_data] == ["TC-LOGIN", "TC-SEARCH", "TC-PLAY"]

    def test_empty_prereqs(self):
        from testagent.plan.replay_manager import _merge_prerequisites

        merged_ids, merged_data = _merge_prerequisites([], ["TC-A"])
        assert merged_ids == ["TC-A"]
        assert merged_data == []

    def test_no_overlap(self):
        from testagent.plan.replay_manager import _merge_prerequisites

        prereqs = [
            (["TC-1"], [{"id": "TC-1"}]),
            (["TC-2"], [{"id": "TC-2"}]),
        ]
        merged_ids, _ = _merge_prerequisites(prereqs, ["TC-3"])
        assert merged_ids == ["TC-1", "TC-2", "TC-3"]


class TestExecuteReplay:
    """execute_replay orchestrates replay execution."""

    @pytest.mark.asyncio
    async def test_replay_pass_resolves_record(self):
        from testagent.plan.replay_manager import execute_replay

        record = FailedCaseReplay(
            app_id="tv.danmaku.bili",
            run_id="run-001",
            test_case_id="TC-001",
            test_case_name="搜索测试",
            original_status="FAILED",
            test_case_data={
                "id": "TC-001",
                "title": "搜索测试",
                "priority": "P1",
                "is_core": False,
                "requirement_ids": [],
                "required_state": [],
                "precondition": None,
                "teardown": [],
                "steps": [{"step": 1, "action": "tap", "target": "搜索", "value": ""}],
            },
            original_run_timestamp=datetime.now(UTC),
        )
        record.id = "record-001"

        mock_repo = AsyncMock()
        mock_repo.get_pending = AsyncMock(return_value=[record])
        mock_repo.update = AsyncMock(return_value=record)

        async def mock_executor(tcs: list[TestCase]) -> list[TestCase]:
            for tc in tcs:
                tc.execution = TCExecution(status=ExecutionStatus.EXECUTED)
            return tcs

        summary = await execute_replay(
            app_id="tv.danmaku.bili",
            repository=mock_repo,
            executor_func=mock_executor,
        )

        assert summary["total_replayed"] == 1
        assert summary["fixed"] == 1
        assert summary["still_failed"] == 0
        assert mock_repo.update.call_count == 2
        # Check the second call is the PASSED result
        result_update = mock_repo.update.call_args_list[1]
        assert result_update[0][1]["last_replay_status"] == "PASSED"
        assert result_update[0][1]["resolved"] == 1

    @pytest.mark.asyncio
    async def test_replay_fail_keeps_record(self):
        from testagent.plan.replay_manager import execute_replay

        record = FailedCaseReplay(
            app_id="tv.danmaku.bili",
            run_id="run-001",
            test_case_id="TC-001",
            test_case_name="搜索测试",
            original_status="FAILED",
            test_case_data={
                "id": "TC-001",
                "title": "搜索测试",
                "priority": "P1",
                "is_core": False,
                "requirement_ids": [],
                "required_state": [],
                "precondition": None,
                "teardown": [],
                "steps": [{"step": 1, "action": "tap", "target": "搜索", "value": ""}],
            },
            original_run_timestamp=datetime.now(UTC),
        )
        record.id = "record-001"

        mock_repo = AsyncMock()
        mock_repo.get_pending = AsyncMock(return_value=[record])
        mock_repo.update = AsyncMock(return_value=record)

        async def mock_executor(tcs: list[TestCase]) -> list[TestCase]:
            for tc in tcs:
                tc.execution = TCExecution(
                    status=ExecutionStatus.FAILED,
                    error_message="Still broken",
                )
            return tcs

        summary = await execute_replay(
            app_id="tv.danmaku.bili",
            repository=mock_repo,
            executor_func=mock_executor,
        )

        assert summary["total_replayed"] == 1
        assert summary["fixed"] == 0
        assert summary["still_failed"] == 1

    @pytest.mark.asyncio
    async def test_no_pending_cases(self):
        from testagent.plan.replay_manager import execute_replay

        mock_repo = AsyncMock()
        mock_repo.get_pending = AsyncMock(return_value=[])

        async def mock_executor(tcs):
            return tcs

        summary = await execute_replay(
            app_id="tv.danmaku.bili",
            repository=mock_repo,
            executor_func=mock_executor,
        )

        assert summary["total_replayed"] == 0

    @pytest.mark.asyncio
    async def test_with_prerequisites_merges(self):
        from testagent.plan.replay_manager import execute_replay

        prereq_record = FailedCaseReplay(
            app_id="tv.danmaku.bili",
            run_id="run-001",
            test_case_id="TC-PRE",
            test_case_name="前置",
            original_status="FAILED",
            test_case_data={
                "id": "TC-PRE", "title": "前置", "priority": "P1",
                "is_core": False, "requirement_ids": [], "required_state": [],
                "precondition": None, "teardown": [],
                "steps": [{"step": 1, "action": "tap", "target": "btn", "value": ""}],
            },
            prerequisite_case_ids=[],
            prerequisite_case_data=[],
            original_run_timestamp=datetime.now(UTC),
        )
        prereq_record.id = "prereq-001"

        target_record = FailedCaseReplay(
            app_id="tv.danmaku.bili",
            run_id="run-001",
            test_case_id="TC-TARGET",
            test_case_name="目标",
            original_status="FAILED",
            test_case_data={
                "id": "TC-TARGET", "title": "目标", "priority": "P1",
                "is_core": False, "requirement_ids": [], "required_state": [],
                "precondition": None, "teardown": [],
                "steps": [{"step": 1, "action": "tap", "target": "btn", "value": ""}],
            },
            prerequisite_case_ids=["TC-PRE"],
            prerequisite_case_data=[{"id": "TC-PRE", "title": "前置"}],
            original_run_timestamp=datetime.now(UTC),
        )
        target_record.id = "target-001"

        mock_repo = AsyncMock()
        mock_repo.get_pending = AsyncMock(return_value=[prereq_record, target_record])
        mock_repo.update = AsyncMock(return_value=target_record)

        executed_ids: list[str] = []

        async def mock_executor(tcs: list[TestCase]) -> list[TestCase]:
            for tc in tcs:
                executed_ids.append(tc.id)
                tc.execution = TCExecution(status=ExecutionStatus.EXECUTED)
            return tcs

        summary = await execute_replay(
            app_id="tv.danmaku.bili",
            repository=mock_repo,
            executor_func=mock_executor,
            with_prerequisites=True,
        )

        assert executed_ids.index("TC-PRE") < executed_ids.index("TC-TARGET")
