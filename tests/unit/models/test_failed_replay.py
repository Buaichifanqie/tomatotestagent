from __future__ import annotations

import pytest
from datetime import UTC, datetime

from testagent.models.failed_replay import REPLAY_STATUSES, FailedCaseReplay


class TestFailedCaseReplayModel:
    """FailedCaseReplay ORM model creation and defaults."""

    def test_table_name(self):
        assert FailedCaseReplay.__tablename__ == "failed_case_replays"

    def test_replay_statuses_constant(self):
        assert REPLAY_STATUSES == ("PENDING", "RUNNING", "PASSED", "STILL_FAILED", "BLOCKED", "SKIPPED")

    def test_create_with_required_fields(self):
        now = datetime.now(UTC)
        record = FailedCaseReplay(
            app_id="tv.danmaku.bili",
            run_id="run-001",
            test_case_id="TC-SEARCH-011",
            test_case_name="搜索-未登录状态下搜索",
            original_status="FAILED",
            test_case_data={"id": "TC-SEARCH-011", "title": "搜索-未登录状态下搜索"},
            original_run_timestamp=now,
        )
        assert record.app_id == "tv.danmaku.bili"
        assert record.run_id == "run-001"
        assert record.test_case_id == "TC-SEARCH-011"
        assert record.original_status == "FAILED"
        assert record.resolved == 0
        assert record.replay_count == 0
        assert record.last_replay_status == "PENDING"

    def test_defaults_for_optional_fields(self):
        now = datetime.now(UTC)
        record = FailedCaseReplay(
            app_id="tv.danmaku.bili",
            run_id="run-001",
            test_case_id="TC-001",
            test_case_name="test",
            original_status="FAILED",
            test_case_data={},
            original_run_timestamp=now,
        )
        assert record.original_error_message is None
        assert record.original_failed_step is None
        assert record.original_screenshot_path is None
        assert record.original_report_path is None
        assert record.prerequisite_case_ids == []
        assert record.prerequisite_case_data == []
        assert record.last_replay_timestamp is None
        assert record.last_replay_error_message is None
        assert record.last_replay_screenshot_path is None
        assert record.last_replay_report_path is None
        assert record.resolved_at is None

    def test_json_fields_accept_dicts_and_lists(self):
        now = datetime.now(UTC)
        tc_data = {"id": "TC-001", "title": "test", "steps": [{"step": 1, "action": "tap"}]}
        prereq_ids = ["TC-PRE-001", "TC-PRE-002"]
        prereq_data = [{"id": "TC-PRE-001"}, {"id": "TC-PRE-002"}]
        record = FailedCaseReplay(
            app_id="tv.danmaku.bili",
            run_id="run-001",
            test_case_id="TC-001",
            test_case_name="test",
            original_status="FAILED",
            test_case_data=tc_data,
            prerequisite_case_ids=prereq_ids,
            prerequisite_case_data=prereq_data,
            original_run_timestamp=now,
        )
        assert record.test_case_data == tc_data
        assert record.prerequisite_case_ids == prereq_ids
        assert record.prerequisite_case_data == prereq_data
