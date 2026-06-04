"""Failed case replay manager.

Captures failures after test execution, manages the replay queue,
and orchestrates replay execution.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from testagent.db.repository import FailedReplayRepository
from testagent.models.failed_replay import FailedCaseReplay
from testagent.plan.models import ExecutionStatus, TestCase

logger = logging.getLogger(__name__)


async def capture_failures(
    executed_tcs: list[TestCase],
    run_id: str,
    app_id: str,
    report_path: str,
    repository: FailedReplayRepository,
) -> None:
    """Capture failures and auto-resolve recovered cases.

    Called at the end of plan execution (Phase 6b).

    Pass 1: Upsert new FAILs into the queue.
    Pass 2: Auto-resolve any queued cases that now PASS.
    """
    now = datetime.now(UTC)

    # Pass 1: Record new failures
    for idx, tc in enumerate(executed_tcs):
        if tc.execution.status not in (ExecutionStatus.FAILED, ExecutionStatus.BLOCKED):
            continue

        # Build prerequisite chain: all TCs before this one in execution order
        prereq_ids: list[str] = []
        prereq_data: list[dict] = []
        for prev_tc in executed_tcs[:idx]:
            if prev_tc.execution.status in (ExecutionStatus.EXECUTED, ExecutionStatus.FAILED):
                prereq_ids.append(prev_tc.id)
                prereq_data.append(prev_tc.model_dump(mode="json"))

        entity = FailedCaseReplay(
            app_id=app_id,
            run_id=run_id,
            test_case_id=tc.id,
            test_case_name=tc.title,
            original_status="BLOCKED" if tc.execution.status == ExecutionStatus.BLOCKED else "FAILED",
            original_error_message=tc.execution.error_message,
            original_failed_step=tc.execution.failed_step,
            original_screenshot_path=None,
            original_report_path=report_path,
            test_case_data=tc.model_dump(mode="json"),
            prerequisite_case_ids=prereq_ids,
            prerequisite_case_data=prereq_data,
            original_run_timestamp=now,
        )

        # Attach screenshot path from the failed step if available
        if tc.execution.failed_step and tc.execution.steps:
            for step_exec in tc.execution.steps:
                if step_exec.step == tc.execution.failed_step:
                    if step_exec.screenshot_after:
                        entity.original_screenshot_path = step_exec.screenshot_after
                    break

        existing = await repository.get_by_app_and_case_id(app_id, tc.id)
        if existing is not None:
            await repository.update(existing.id, {
                "run_id": run_id,
                "test_case_name": tc.title,
                "original_status": entity.original_status,
                "original_error_message": entity.original_error_message,
                "original_failed_step": entity.original_failed_step,
                "original_screenshot_path": entity.original_screenshot_path,
                "original_report_path": report_path,
                "test_case_data": entity.test_case_data,
                "prerequisite_case_ids": entity.prerequisite_case_ids,
                "prerequisite_case_data": entity.prerequisite_case_data,
                "original_run_timestamp": now,
                "last_replay_status": "PENDING",
            })
            logger.info("Updated failure: %s (%s)", tc.id, tc.title)
        else:
            await repository.create(entity)
            logger.info("Captured failure: %s (%s)", tc.id, tc.title)

    # Pass 2: Auto-resolve recovered cases
    for tc in executed_tcs:
        if tc.execution.status not in (ExecutionStatus.EXECUTED,):
            continue

        existing = await repository.get_by_app_and_case_id(app_id, tc.id)
        if existing is not None:
            await repository.update(existing.id, {
                "resolved": 1,
                "resolved_at": now,
                "last_replay_status": "PASSED",
                "last_replay_timestamp": now,
            })
            logger.info("Auto-resolved: %s (was %s)", tc.id, existing.original_status)


async def get_pending(
    app_id: str,
    repository: FailedReplayRepository,
    include_blocked: bool = False,
) -> list[FailedCaseReplay]:
    """Get pending failed cases for an app."""
    return await repository.get_pending(app_id, include_blocked=include_blocked)


def _merge_prerequisites(
    prereqs: list[tuple[list[str], list[dict]]],
    target_ids: list[str],
) -> tuple[list[str], list[dict]]:
    """Merge prerequisite chains preserving order, deduplicating by ID.

    Returns (merged_ids, merged_data) with target_ids appended at the end.
    """
    seen: set[str] = set()
    merged_ids: list[str] = []
    merged_data: list[dict] = []

    for ids, data in prereqs:
        for i, tc_id in enumerate(ids):
            if tc_id not in seen:
                seen.add(tc_id)
                merged_ids.append(tc_id)
                if i < len(data):
                    merged_data.append(data[i])

    for tc_id in target_ids:
        if tc_id not in seen:
            seen.add(tc_id)
            merged_ids.append(tc_id)

    return merged_ids, merged_data


async def execute_replay(
    app_id: str,
    repository: FailedReplayRepository,
    executor_func,
    case_ids: list[str] | None = None,
    with_prerequisites: bool = False,
    report_dir: str = "reports/delta",
) -> dict:
    """Execute replay of pending failed cases.

    Args:
        app_id: App identifier.
        repository: FailedReplayRepository instance.
        executor_func: Async callable that takes list[TestCase] and returns list[TestCase].
        case_ids: Optional filter to replay only specific cases.
        with_prerequisites: Whether to execute prerequisite chain first.
        report_dir: Directory for delta reports.

    Returns:
        Summary dict with keys: total_replayed, fixed, still_failed, blocked, skipped, details.
    """
    pending = await repository.get_pending(app_id, include_blocked=False)
    if case_ids:
        pending = [r for r in pending if r.test_case_id in case_ids]

    if not pending:
        return {
            "total_replayed": 0, "fixed": 0, "still_failed": 0,
            "blocked": 0, "skipped": 0,
            "details": {"fixed": [], "still_failed": [], "blocked": [], "skipped": []},
        }

    # Mark all as RUNNING
    now = datetime.now(UTC)
    for record in pending:
        await repository.update(record.id, {
            "last_replay_status": "RUNNING",
            "last_replay_timestamp": now,
        })

    # Build execution list
    if with_prerequisites:
        prereqs = [
            (r.prerequisite_case_ids, r.prerequisite_case_data)
            for r in pending
        ]
        target_ids = [r.test_case_id for r in pending]
        _, merged_data = _merge_prerequisites(prereqs, target_ids)

        tc_dicts: list[dict] = []
        seen: set[str] = set()
        for d in merged_data:
            if d["id"] not in seen:
                seen.add(d["id"])
                tc_dicts.append(d)
        for r in pending:
            if r.test_case_id not in seen:
                tc_dicts.append(r.test_case_data)
    else:
        tc_dicts = [r.test_case_data for r in pending]

    # Rebuild TestCase objects
    test_cases = [TestCase.model_validate(d) for d in tc_dicts]

    # Execute
    executed_tcs = await executor_func(test_cases)

    # Compare results and update records
    executed_map = {tc.id: tc for tc in executed_tcs}
    summary: dict = {
        "total_replayed": 0, "fixed": 0, "still_failed": 0,
        "blocked": 0, "skipped": 0,
        "details": {"fixed": [], "still_failed": [], "blocked": [], "skipped": []},
    }

    for record in pending:
        tc = executed_map.get(record.test_case_id)
        if tc is None:
            await repository.update(record.id, {
                "last_replay_status": "SKIPPED",
                "replay_count": record.replay_count + 1,
            })
            summary["skipped"] += 1
            summary["details"]["skipped"].append(record.test_case_id)
            continue

        summary["total_replayed"] += 1
        replay_status = "STILL_FAILED"
        update_data: dict = {
            "replay_count": record.replay_count + 1,
            "last_replay_timestamp": now,
        }

        if tc.execution.status == ExecutionStatus.EXECUTED:
            replay_status = "PASSED"
            update_data["resolved"] = 1
            update_data["resolved_at"] = now
            summary["fixed"] += 1
            summary["details"]["fixed"].append(record.test_case_id)
        elif tc.execution.status == ExecutionStatus.BLOCKED:
            replay_status = "BLOCKED"
            summary["blocked"] += 1
            summary["details"]["blocked"].append(record.test_case_id)
        else:
            update_data["last_replay_error_message"] = tc.execution.error_message
            summary["still_failed"] += 1
            summary["details"]["still_failed"].append(record.test_case_id)

        update_data["last_replay_status"] = replay_status
        await repository.update(record.id, update_data)

    return summary
