from __future__ import annotations

import json
import os
import pytest
import tempfile

from testagent.plan.delta_report import DeltaReportGenerator


class TestDeltaReportGenerator:
    """DeltaReportGenerator produces JSON and HTML reports."""

    def test_json_structure(self):
        gen = DeltaReportGenerator()
        summary = {
            "total_replayed": 3,
            "fixed": 2,
            "still_failed": 1,
            "blocked": 0,
            "skipped": 0,
            "details": {
                "fixed": ["TC-001", "TC-002"],
                "still_failed": ["TC-003"],
                "blocked": [],
                "skipped": [],
            },
        }
        records = [
            {
                "test_case_id": "TC-001",
                "test_case_name": "搜索测试",
                "original_error_message": "Element not found",
                "last_replay_status": "PASSED",
                "replay_count": 1,
            },
            {
                "test_case_id": "TC-002",
                "test_case_name": "播放测试",
                "original_error_message": "Button disabled",
                "last_replay_status": "PASSED",
                "replay_count": 1,
            },
            {
                "test_case_id": "TC-003",
                "test_case_name": "评论测试",
                "original_error_message": "Timeout",
                "last_replay_error_message": "Still timeout",
                "last_replay_status": "STILL_FAILED",
                "replay_count": 2,
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            path = gen.generate_json("tv.danmaku.bili", summary, records, tmpdir)
            assert os.path.exists(path)
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            assert data["app_id"] == "tv.danmaku.bili"
            assert data["summary"]["total_replayed"] == 3
            assert data["summary"]["fixed"] == 2
            assert len(data["details"]["fixed"]) == 2
            assert len(data["details"]["still_failed"]) == 1

    def test_html_structure(self):
        gen = DeltaReportGenerator()
        summary = {
            "total_replayed": 2,
            "fixed": 1,
            "still_failed": 1,
            "blocked": 0,
            "skipped": 0,
            "details": {
                "fixed": ["TC-001"],
                "still_failed": ["TC-002"],
                "blocked": [],
                "skipped": [],
            },
        }
        records = [
            {
                "test_case_id": "TC-001",
                "test_case_name": "搜索测试",
                "original_error_message": "Element not found",
                "last_replay_status": "PASSED",
                "replay_count": 1,
            },
            {
                "test_case_id": "TC-002",
                "test_case_name": "评论测试",
                "original_error_message": "Timeout",
                "last_replay_error_message": "Still timeout",
                "last_replay_status": "STILL_FAILED",
                "replay_count": 1,
            },
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            path = gen.generate_html("tv.danmaku.bili", summary, records, tmpdir)
            assert os.path.exists(path)
            with open(path, encoding="utf-8") as f:
                html = f.read()
            assert "tv.danmaku.bili" in html
            assert "TC-001" in html
            assert "TC-002" in html
            assert "搜索测试" in html
            assert "Fixed" in html or "fixed" in html.lower()

    def test_both_formats(self):
        gen = DeltaReportGenerator()
        summary = {
            "total_replayed": 0, "fixed": 0, "still_failed": 0,
            "blocked": 0, "skipped": 0,
            "details": {"fixed": [], "still_failed": [], "blocked": [], "skipped": []},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            json_path, html_path = gen.generate("tv.danmaku.bili", summary, [], tmpdir)
            assert json_path.endswith(".json")
            assert html_path.endswith(".html")
            assert os.path.exists(json_path)
            assert os.path.exists(html_path)
