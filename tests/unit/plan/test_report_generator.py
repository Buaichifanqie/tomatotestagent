from __future__ import annotations

from pathlib import Path

import pytest

from testagent.plan.models import (
    ExecutionStatus,
    ExecutionVerdict,
    OverallEvaluation,
    PlanConfig,
    StepExecution,
    TCExecution,
    TestCase,
)
from testagent.plan.report_generator import ReportGenerator


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_tc(
    tc_id: str = "TC-001",
    title: str = "Sample test",
    priority: str = "P1",
    is_core: bool = False,
    status: ExecutionStatus = ExecutionStatus.EXECUTED,
    verdict: ExecutionVerdict = ExecutionVerdict.PASS,
    steps: list[StepExecution] | None = None,
    duration_ms: int = 1000,
    error_message: str = "",
) -> TestCase:
    execution = TCExecution(
        status=status,
        verdict=verdict,
        steps=steps or [],
        duration_ms=duration_ms,
        error_message=error_message,
    )
    return TestCase(
        id=tc_id,
        title=title,
        priority=priority,
        is_core=is_core,
        execution=execution,
    )


def _make_step(
    step: int,
    action: str = "click",
    target: str = "button",
    success: bool = True,
    error_message: str = "",
    duration_ms: int = 500,
) -> StepExecution:
    return StepExecution(
        step=step,
        action=action,
        target=target,
        success=success,
        error_message=error_message,
        duration_ms=duration_ms,
    )


# ── tests ────────────────────────────────────────────────────────────────────


class TestReportGenerator:
    def test_generate_minimal(self, tmp_path: Path) -> None:
        """Empty TCs, check file is created at expected path."""
        output_dir = tmp_path / "reports"
        gen = ReportGenerator(str(output_dir))
        overall = OverallEvaluation(
            verdict=ExecutionVerdict.PASS,
            total_count=0,
            passed_count=0,
        )
        config = PlanConfig()
        result = gen.generate("test-plan", [], overall, config)
        expected = str(output_dir / "plan-report.md")
        assert result == expected
        assert Path(expected).exists()
        content = Path(expected).read_text(encoding="utf-8")
        assert "# 测试报告" in content
        assert "test-plan" in content

    def test_report_content(self, tmp_path: Path) -> None:
        """One TC with steps, verify content contains key strings."""
        output_dir = tmp_path / "reports"
        gen = ReportGenerator(str(output_dir))
        steps = [
            _make_step(1, action="click", target="login_btn"),
            _make_step(2, action="input", target="username"),
        ]
        tc = _make_tc(steps=steps)
        overall = OverallEvaluation(
            verdict=ExecutionVerdict.PASS,
            total_count=1,
            passed_count=1,
        )
        config = PlanConfig()
        result = gen.generate("test-plan", [tc], overall, config)
        content = Path(result).read_text(encoding="utf-8")
        assert "## 总体评估" in content
        assert "## 测试结果汇总" in content
        assert "## 详细执行记录" in content
        assert "## 需人工复查的用例" in content
        assert "TC-001" in content
        assert "Sample test" in content
        assert "login_btn" in content
        assert "username" in content
        assert "1/1" in content

    def test_report_with_failed_tc(self, tmp_path: Path) -> None:
        """Failed TC appears correctly."""
        output_dir = tmp_path / "reports"
        gen = ReportGenerator(str(output_dir))
        step = _make_step(
            step=1,
            action="click",
            target="submit",
            success=False,
            error_message="Element not found",
        )
        tc = _make_tc(
            tc_id="TC-002",
            title="Failing test",
            status=ExecutionStatus.FAILED,
            verdict=ExecutionVerdict.FAIL,
            steps=[step],
            error_message="Step 1 failed: Element not found",
        )
        overall = OverallEvaluation(
            verdict=ExecutionVerdict.FAIL,
            total_count=1,
            passed_count=0,
        )
        config = PlanConfig()
        result = gen.generate("test-plan", [tc], overall, config)
        content = Path(result).read_text(encoding="utf-8")
        assert "❌ FAIL" in content
        assert "Step 1 failed" in content
        assert "Element not found" in content
        assert "0/1" in content

    def test_report_with_multiple_tcs(self, tmp_path: Path) -> None:
        """Table has correct number of rows."""
        output_dir = tmp_path / "reports"
        gen = ReportGenerator(str(output_dir))
        tcs = [
            _make_tc(tc_id="TC-001", title="Test 1"),
            _make_tc(
                tc_id="TC-002",
                title="Test 2",
                verdict=ExecutionVerdict.FAIL,
                status=ExecutionStatus.FAILED,
            ),
            _make_tc(tc_id="TC-003", title="Test 3"),
        ]
        overall = OverallEvaluation(
            verdict=ExecutionVerdict.PARTIAL,
            total_count=3,
            passed_count=2,
        )
        config = PlanConfig()
        result = gen.generate("test-plan", tcs, overall, config)
        content = Path(result).read_text(encoding="utf-8")
        assert content.count("TC-001") >= 1
        assert content.count("TC-002") >= 1
        assert content.count("TC-003") >= 1

    @pytest.mark.parametrize(
        ("verdict", "expected"),
        [
            (ExecutionVerdict.PASS, "✅ PASS"),
            (ExecutionVerdict.FAIL, "❌ FAIL"),
            (ExecutionVerdict.BLOCKED, "⛔ BLOCKED"),
            (ExecutionVerdict.NEED_REVIEW, "⚠️ NEED_REVIEW"),
            (ExecutionVerdict.INCONCLUSIVE, "❓ INCONCLUSIVE"),
            (ExecutionVerdict.PARTIAL, "⚠️ PARTIAL"),
        ],
    )
    def test_verdict_badge(self, verdict: ExecutionVerdict, expected: str) -> None:
        """All verdict types produce correct badge string."""
        assert ReportGenerator._verdict_badge(verdict) == expected

    def test_output_dir_created(self, tmp_path: Path) -> None:
        """Directory is created if it doesn't exist."""
        output_dir = tmp_path / "nonexistent" / "deep" / "reports"
        assert not output_dir.exists()
        gen = ReportGenerator(str(output_dir))
        overall = OverallEvaluation(
            verdict=ExecutionVerdict.PASS,
            total_count=0,
            passed_count=0,
        )
        config = PlanConfig()
        result = gen.generate("test-plan", [], overall, config)
        assert output_dir.exists()
        assert Path(result).exists()
