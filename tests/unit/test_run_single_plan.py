"""Tests for run_single_plan() and PlanResult."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch

from testagent.cli.plan import PlanResult, run_single_plan


class TestPlanResult:
    def test_default_values(self):
        result = PlanResult(status="completed", requirement_source="test.md")
        assert result.status == "completed"
        assert result.requirement_source == "test.md"
        assert result.test_cases == []
        assert result.report_path == ""
        assert result.summary == ""
        assert result.error is None
        assert result.case_count == 0
        assert result.passed == 0
        assert result.failed == 0
        assert result.duration == ""

    def test_failed_result(self):
        result = PlanResult(
            status="failed",
            requirement_source="bad.md",
            error="File not found",
        )
        assert result.status == "failed"
        assert result.error == "File not found"


class TestRunSinglePlan:
    @pytest.mark.asyncio
    async def test_returns_failed_on_exception(self):
        with patch(
            "testagent.cli.plan._plan_command_async",
            new_callable=AsyncMock,
            side_effect=RuntimeError("LLM crashed"),
        ):
            result = await run_single_plan("doc.md", auto_yes=True)
            assert result.status == "failed"
            assert "LLM crashed" in result.error

    @pytest.mark.asyncio
    async def test_returns_failed_when_no_report(self):
        with patch(
            "testagent.cli.plan._plan_command_async",
            new_callable=AsyncMock,
            return_value=(None, None, []),
        ):
            result = await run_single_plan("doc.md", auto_yes=True)
            assert result.status == "failed"
            assert "aborted" in result.error.lower()

    @pytest.mark.asyncio
    async def test_returns_completed_with_overall_eval(self):
        """Test that stats come from OverallEvaluation, not regex parsing."""
        from testagent.plan.models import OverallEvaluation, ExecutionVerdict

        mock_overall = OverallEvaluation(
            verdict=ExecutionVerdict.PASS,
            total_count=5,
            passed_count=4,
            core_total=3,
            core_passed=3,
            need_review_count=0,
            blocked_count=0,
            summary="Overall: PASS | Passed 4/5",
            review_recommendations=[],
        )

        with patch(
            "testagent.cli.plan._plan_command_async",
            new_callable=AsyncMock,
            return_value=("/fake/report.md", mock_overall, []),
        ):
            result = await run_single_plan("doc.md", auto_yes=True)
            assert result.status == "completed"
            assert result.case_count == 5
            assert result.passed == 4
            assert result.failed == 1
            assert result.report_path == "/fake/report.md"
            assert "5 test cases" in result.summary

    @pytest.mark.asyncio
    async def test_log_fn_called(self):
        logs = []
        with patch(
            "testagent.cli.plan._plan_command_async",
            new_callable=AsyncMock,
            side_effect=RuntimeError("fail"),
        ):
            await run_single_plan("doc.md", auto_yes=True, log_fn=logs.append)
            assert any("Starting plan" in msg for msg in logs)
            assert any("failed" in msg.lower() for msg in logs)

    @pytest.mark.asyncio
    async def test_populates_test_cases(self):
        """Test that PlanResult.test_cases is populated from executed_tcs."""
        from testagent.plan.models import (
            TestCase,
            TestStep,
            TCExecution,
            ExecutionStatus,
            OverallEvaluation,
            ExecutionVerdict,
        )

        mock_tc = TestCase(
            id="TC-001",
            title="Test login",
            priority="P1",
            is_core=True,
            steps=[TestStep(step=1, action="launch", target="", value="")],
        )
        mock_tc.execution.status = ExecutionStatus.EXECUTED

        mock_overall = OverallEvaluation(
            verdict=ExecutionVerdict.PASS,
            total_count=1,
            passed_count=1,
            core_total=1,
            core_passed=1,
            need_review_count=0,
            blocked_count=0,
            summary="Overall: PASS | Passed 1/1",
            review_recommendations=[],
        )

        with patch(
            "testagent.cli.plan._plan_command_async",
            new_callable=AsyncMock,
            return_value=("/fake/report.md", mock_overall, [mock_tc]),
        ):
            result = await run_single_plan("doc.md", auto_yes=True)
            assert len(result.test_cases) == 1
            assert result.test_cases[0].id == "TC-001"
