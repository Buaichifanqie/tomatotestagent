"""End-to-end unit test for the failed case replay flow.

Tests: capture_failures -> get_pending -> execute_replay -> delta report.
All DB calls are mocked -- this is a logic integration test, not a DB test.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from testagent.models.failed_replay import FailedCaseReplay
from testagent.plan.models import ExecutionStatus, TCExecution, TestCase, TestStep
from testagent.plan.replay_manager import capture_failures, execute_replay, get_pending
from testagent.plan.delta_report import DeltaReportGenerator


def _make_tc(tc_id: str, title: str, status: ExecutionStatus) -> TestCase:
    tc = TestCase(
        id=tc_id, title=title,
        steps=[TestStep(step=1, action="tap", target="btn", value="")],
    )
    tc.execution = TCExecution(status=status, error_message="err" if status == ExecutionStatus.FAILED else "")
    return tc


@pytest.mark.asyncio
async def test_full_replay_flow():
    """Capture failures -> replay -> generate delta report."""
    # -- Setup: mock repository with in-memory storage ---------------
    storage: dict[str, FailedCaseReplay] = {}

    mock_repo = AsyncMock()

    async def mock_get_by_app_case(app_id, tc_id):
        for r in storage.values():
            if r.app_id == app_id and r.test_case_id == tc_id and r.resolved == 0:
                return r
        return None

    async def mock_create(entity):
        entity.id = f"rec-{len(storage)}"
        storage[entity.id] = entity
        return entity

    async def mock_update(entity_id, data):
        rec = storage.get(entity_id)
        if rec:
            for k, v in data.items():
                setattr(rec, k, v)
        return rec

    async def mock_get_pending(app_id, include_blocked=False):
        return [r for r in storage.values() if r.app_id == app_id and r.resolved == 0 and r.original_status == "FAILED"]

    mock_repo.get_by_app_and_case_id = mock_get_by_app_case
    mock_repo.create = mock_create
    mock_repo.update = mock_update
    mock_repo.get_pending = mock_get_pending

    # -- Step 1: Capture failures from a test run --------------------
    run_tcs = [
        _make_tc("TC-001", "搜索测试", ExecutionStatus.EXECUTED),
        _make_tc("TC-002", "评论测试", ExecutionStatus.FAILED),
        _make_tc("TC-003", "点赞测试", ExecutionStatus.FAILED),
    ]

    await capture_failures(
        executed_tcs=run_tcs,
        run_id="run-001",
        app_id="tv.danmaku.bili",
        report_path="/reports/run1.html",
        repository=mock_repo,
    )

    assert len(storage) == 2  # Two failures captured

    # -- Step 2: Verify pending cases --------------------------------
    pending = await get_pending("tv.danmaku.bili", mock_repo)
    assert len(pending) == 2

    # -- Step 3: Replay -- one passes, one still fails ---------------
    async def mock_executor(tcs: list[TestCase]) -> list[TestCase]:
        for tc in tcs:
            if tc.id == "TC-002":
                tc.execution = TCExecution(status=ExecutionStatus.EXECUTED)
            else:
                tc.execution = TCExecution(status=ExecutionStatus.FAILED, error_message="Still broken")
        return tcs

    summary = await execute_replay(
        app_id="tv.danmaku.bili",
        repository=mock_repo,
        executor_func=mock_executor,
    )

    assert summary["total_replayed"] == 2
    assert summary["fixed"] == 1
    assert summary["still_failed"] == 1

    # -- Step 4: Verify TC-002 is resolved ---------------------------
    tc002_record = await mock_get_by_app_case("tv.danmaku.bili", "TC-002")
    assert tc002_record is None  # resolved=1, so get_pending filtered it out
    # But it still exists in storage
    resolved_rec = [r for r in storage.values() if r.test_case_id == "TC-002"][0]
    assert resolved_rec.resolved == 1
    assert resolved_rec.last_replay_status == "PASSED"

    # -- Step 5: Generate delta report -------------------------------
    gen = DeltaReportGenerator()
    report_records = [
        {
            "test_case_id": "TC-002", "test_case_name": "评论测试",
            "original_error_message": "err", "last_replay_error_message": None,
            "last_replay_status": "PASSED", "replay_count": 1,
        },
        {
            "test_case_id": "TC-003", "test_case_name": "点赞测试",
            "original_error_message": "err", "last_replay_error_message": "Still broken",
            "last_replay_status": "STILL_FAILED", "replay_count": 1,
        },
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        json_path, html_path = gen.generate("tv.danmaku.bili", summary, report_records, tmpdir)
        assert os.path.exists(json_path)
        assert os.path.exists(html_path)

        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)
        assert data["summary"]["fixed"] == 1
        assert data["summary"]["still_failed"] == 1


@pytest.mark.asyncio
async def test_auto_resolve_on_full_pass():
    """A full run that passes a queued case auto-resolves it."""
    existing = FailedCaseReplay(
        app_id="tv.danmaku.bili", run_id="run-old",
        test_case_id="TC-001", test_case_name="搜索",
        original_status="FAILED", test_case_data={},
        original_run_timestamp=datetime.now(UTC),
    )
    existing.id = "rec-001"
    existing.resolved = 0

    mock_repo = AsyncMock()
    mock_repo.get_by_app_and_case_id = AsyncMock(return_value=existing)
    mock_repo.update = AsyncMock(return_value=existing)
    mock_repo.create = AsyncMock()

    passed_tc = _make_tc("TC-001", "搜索", ExecutionStatus.EXECUTED)

    await capture_failures(
        executed_tcs=[passed_tc],
        run_id="run-002",
        app_id="tv.danmaku.bili",
        report_path="/reports/run2.html",
        repository=mock_repo,
    )

    # Should NOT create a new record
    mock_repo.create.assert_not_called()
    # Should auto-resolve the existing record
    mock_repo.update.assert_called_once()
    update_data = mock_repo.update.call_args[0][1]
    assert update_data["resolved"] == 1
    assert update_data["last_replay_status"] == "PASSED"
