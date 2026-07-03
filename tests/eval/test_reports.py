"""Tests for eval reporters (MarkdownReporter, JsonReporter)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from testagent.eval.models import (
    GraderResult,
    SuiteResult,
    TaskResult,
    Transcript,
    TranscriptSummary,
    TrialResult,
)
from testagent.eval.reports.json_reporter import JsonReporter
from testagent.eval.reports.markdown_reporter import MarkdownReporter


# ── Sample data factory ──────────────────────────────────────────────────────


def _make_sample_result() -> SuiteResult:
    """Build a realistic SuiteResult with two tasks for testing."""
    return SuiteResult(
        suite_name="bilibili",
        run_id="eval_bilibili_20260703_001",
        timestamp="2026-07-03T14:30:22",
        duration=755.0,
        model_name="deepseek-v4-flash",
        task_results=[
            TaskResult(
                task_id="bilibili_search_basic",
                trials=[
                    TrialResult(
                        trial_num=0,
                        passed=True,
                        score=0.95,
                        grader_results=[
                            GraderResult("state_check", 1.0, True),
                            GraderResult("llm_rubric", 0.9, True),
                        ],
                        transcript=Transcript(
                            summary=TranscriptSummary(
                                n_turns=4,
                                n_tool_calls=3,
                                total_tokens=12345,
                            ),
                            messages=[
                                {"role": "user", "content": "搜索 bilibili"},
                                {"role": "assistant", "content": "正在打开 bilibili"},
                                {"role": "user", "content": "搜索 'hello'"},
                                {
                                    "role": "assistant",
                                    "content": "搜索结果已加载",
                                },
                            ],
                        ),
                    ),
                    TrialResult(
                        trial_num=1,
                        passed=False,
                        score=0.35,
                        failure_reason="Results not loaded",
                        transcript=Transcript(
                            summary=TranscriptSummary(
                                n_turns=2,
                                n_tool_calls=1,
                                total_tokens=5432,
                            ),
                            messages=[
                                {"role": "user", "content": "搜索空关键词"},
                                {
                                    "role": "assistant",
                                    "content": "返回空结果页面",
                                },
                            ],
                        ),
                    ),
                ],
            ),
            TaskResult(
                task_id="bilibili_search_empty",
                trials=[
                    TrialResult(
                        trial_num=0,
                        passed=False,
                        score=0.2,
                        failure_reason="Empty search not handled",
                    ),
                ],
            ),
        ],
    )


# ── Markdown Reporter tests ──────────────────────────────────────────────────


class TestMarkdownReporter:
    def test_generate_minimal(self) -> None:
        """Verify key structural elements appear in the report."""
        result = _make_sample_result()
        report = MarkdownReporter.generate(result)

        # Header
        assert "# 评测报告" in report
        assert "bilibili" in report
        assert "eval_bilibili_20260703_001" in report
        assert "deepseek-v4-flash" in report

        # Section headers
        assert "## 1. 总体概览" in report
        assert "## 2. 逐任务详情" in report
        assert "## 3. 稳定性分析" in report
        assert "## 4. 性能指标" in report
        assert "## 5. 历史对比" in report
        assert "## 6. Transcript 示例" in report
        assert "## 7. 改进建议" in report

        # Task names should appear
        assert "bilibili_search_basic" in report
        assert "bilibili_search_empty" in report

        # Performance metrics headers
        assert "平均 tokens" in report
        assert "平均 turns" in report
        assert "平均 tool_calls" in report

        # Overview table values
        assert "50.0%" in report  # pass_at_1_rate = 1/2
        assert "50.0%" in report  # overall_pass_rate = 1/2
        assert "0.0%" in report  # pass_k_rate = 0/2

        # Stability labels
        assert "稳定" in report
        assert "系统性问题" in report

        # Suggestion section
        assert "改进建议" in report

        # Transcript section — first failed trial's messages
        assert "搜索空关键词" in report

    def test_save_report(self) -> None:
        """Save to a temp directory and verify the file exists."""
        result = _make_sample_result()
        with tempfile.TemporaryDirectory() as tmp:
            path = MarkdownReporter.save(result, Path(tmp))
            assert path.endswith("report.md")
            saved = Path(path)
            assert saved.exists()
            content = saved.read_text(encoding="utf-8")
            assert len(content) > 200
            assert "# 评测报告" in content

    def test_empty_suite(self) -> None:
        """An empty suite should produce a report without crashing."""
        result = SuiteResult(
            suite_name="empty",
            run_id="empty_run",
            timestamp="2026-07-03T00:00:00",
            task_results=[],
        )
        report = MarkdownReporter.generate(result)
        assert "# 评测报告" in report
        assert "0" in report  # zero tasks

        # Sections should still render
        assert "## 1. 总体概览" in report
        assert "## 2. 逐任务详情" in report

        # No suggestions needed
        assert "无需改进" in report


# ── JSON Reporter tests ──────────────────────────────────────────────────────


class TestJsonReporter:
    def test_generate(self) -> None:
        """Verify JSON dict has correct keys and values."""
        result = _make_sample_result()
        data = JsonReporter.generate(result)

        assert data["suite_name"] == "bilibili"
        assert data["run_id"] == "eval_bilibili_20260703_001"
        assert data["model_name"] == "deepseek-v4-flash"
        assert data["duration"] == 755.0
        assert data["overall_pass_rate"] == 0.5  # 1/2 tasks pass_at_k
        assert data["pass_at_1_rate"] == 0.5  # 1/2 tasks pass_at_1
        assert data["pass_k_rate"] == 0.0  # 0/2 tasks all_passed
        assert data["num_tasks"] == 2

        tasks = data["task_results"]
        assert len(tasks) == 2

        # bilibili_search_basic
        t0 = tasks[0]
        assert t0["task_id"] == "bilibili_search_basic"
        assert t0["pass_at_1"] is True
        assert t0["pass_at_k"] is True
        assert t0["all_passed"] is False
        assert t0["mean_score"] == pytest.approx(0.65, rel=1e-4)  # (0.95 + 0.35) / 2
        assert t0["num_trials"] == 2

        # bilibili_search_empty
        t1 = tasks[1]
        assert t1["task_id"] == "bilibili_search_empty"
        assert t1["pass_at_1"] is False
        assert t1["pass_at_k"] is False
        assert t1["all_passed"] is False
        assert t1["mean_score"] == 0.2
        assert t1["num_trials"] == 1

    def test_save(self) -> None:
        """Save JSON to a file and verify its content."""
        result = _make_sample_result()
        with tempfile.TemporaryDirectory() as tmp:
            path = JsonReporter.save(result, Path(tmp))
            assert path.endswith("summary.json")
            saved = Path(path)
            assert saved.exists()

            data = json.loads(saved.read_text(encoding="utf-8"))
            assert data["suite_name"] == "bilibili"
            assert data["num_tasks"] == 2

    def test_empty_suite(self) -> None:
        """Empty suite should serialise without errors."""
        result = SuiteResult(
            suite_name="empty",
            run_id="empty_run",
            timestamp="",
            task_results=[],
        )
        data = JsonReporter.generate(result)
        assert data["num_tasks"] == 0
        assert data["overall_pass_rate"] == 0.0
        assert data["pass_at_1_rate"] == 0.0
        assert data["pass_k_rate"] == 0.0
