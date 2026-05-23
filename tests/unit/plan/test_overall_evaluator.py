from __future__ import annotations

import pytest

from testagent.plan.models import (
    ExecutionStatus,
    ExecutionVerdict,
    OverallEvaluation,
    TestCase,
    TestStep,
)
from testagent.plan.overall_evaluator import OverallEvaluator


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_tc(
    id: str,
    priority: str = "P1",
    is_core: bool = False,
    status: ExecutionStatus = ExecutionStatus.EXECUTED,
    verdict: ExecutionVerdict | None = ExecutionVerdict.PASS,
) -> TestCase:
    tc = TestCase(
        id=id, title=id, steps=[TestStep(step=1, action="tap", target="x")]
    )
    tc.priority = priority
    tc.is_core = is_core
    tc.execution.status = status
    tc.execution.verdict = verdict
    return tc


# ── tests ────────────────────────────────────────────────────────────────────


class TestOverallEvaluator:
    def test_all_pass(self) -> None:
        """All TCs PASS -> overall PASS."""
        tcs = [
            _make_tc("TC-1", verdict=ExecutionVerdict.PASS),
            _make_tc("TC-2", verdict=ExecutionVerdict.PASS),
            _make_tc("TC-3", verdict=ExecutionVerdict.PASS),
        ]
        ev = OverallEvaluator()
        result = ev.evaluate(tcs)
        assert isinstance(result, OverallEvaluation)
        assert result.verdict == ExecutionVerdict.PASS
        assert result.total_count == 3
        assert result.passed_count == 3

    def test_core_fail(self) -> None:
        """Core TC FAIL -> overall FAIL."""
        tcs = [
            _make_tc("TC-1", is_core=True, verdict=ExecutionVerdict.FAIL),
            _make_tc("TC-2", verdict=ExecutionVerdict.PASS),
        ]
        ev = OverallEvaluator()
        result = ev.evaluate(tcs)
        assert result.verdict == ExecutionVerdict.FAIL

    def test_core_all_pass_non_core_fail(self) -> None:
        """Core all PASS, non-core FAIL -> PARTIAL."""
        tcs = [
            _make_tc("TC-1", is_core=True, verdict=ExecutionVerdict.PASS),
            _make_tc("TC-2", is_core=True, verdict=ExecutionVerdict.PASS),
            _make_tc("TC-3", is_core=False, verdict=ExecutionVerdict.FAIL),
        ]
        ev = OverallEvaluator()
        result = ev.evaluate(tcs)
        assert result.verdict == ExecutionVerdict.PARTIAL

    def test_all_blocked(self) -> None:
        """All BLOCKED -> INCONCLUSIVE."""
        tcs = [
            _make_tc("TC-1", verdict=ExecutionVerdict.BLOCKED),
            _make_tc("TC-2", verdict=ExecutionVerdict.BLOCKED),
            _make_tc("TC-3", verdict=ExecutionVerdict.BLOCKED),
        ]
        ev = OverallEvaluator()
        result = ev.evaluate(tcs)
        assert result.verdict == ExecutionVerdict.INCONCLUSIVE

    def test_mixed_statistics(self) -> None:
        """Mixed results produce correct statistics."""
        tcs = [
            _make_tc("TC-1", is_core=True, verdict=ExecutionVerdict.PASS),
            _make_tc("TC-2", is_core=True, verdict=ExecutionVerdict.PASS),
            _make_tc("TC-3", is_core=False, verdict=ExecutionVerdict.FAIL),
            _make_tc("TC-4", is_core=False, verdict=ExecutionVerdict.BLOCKED),
            _make_tc("TC-5", is_core=True, verdict=ExecutionVerdict.NEED_REVIEW),
        ]
        ev = OverallEvaluator()
        result = ev.evaluate(tcs)
        assert result.total_count == 5
        assert result.passed_count == 2
        assert result.core_total == 3
        assert result.core_passed == 2
        assert result.blocked_count == 1
        assert result.need_review_count == 1

    def test_pass_rate_and_core_pass_rate(self) -> None:
        """pass_rate and core_pass_rate properties."""
        tcs = [
            _make_tc("TC-1", is_core=True, verdict=ExecutionVerdict.PASS),
            _make_tc("TC-2", is_core=True, verdict=ExecutionVerdict.PASS),
            _make_tc("TC-3", is_core=False, verdict=ExecutionVerdict.FAIL),
        ]
        ev = OverallEvaluator()
        result = ev.evaluate(tcs)
        assert result.pass_rate == "2/3"
        assert result.core_pass_rate == "2/2"

    def test_empty_list(self) -> None:
        """Empty TC list does not crash."""
        ev = OverallEvaluator()
        result = ev.evaluate([])
        assert isinstance(result, OverallEvaluation)
        assert result.total_count == 0

    def test_no_core_cases(self) -> None:
        """No core TCs -> core_pass_rate is N/A."""
        tcs = [
            _make_tc("TC-1", is_core=False, verdict=ExecutionVerdict.PASS),
            _make_tc("TC-2", is_core=False, verdict=ExecutionVerdict.PASS),
        ]
        ev = OverallEvaluator()
        result = ev.evaluate(tcs)
        assert result.core_pass_rate == "N/A"
        assert result.core_total == 0

    def test_review_recommendations(self) -> None:
        """review_recommendations contains FAIL and NEED_REVIEW TCs."""
        tcs = [
            _make_tc("TC-1", verdict=ExecutionVerdict.PASS),
            _make_tc("TC-2", verdict=ExecutionVerdict.FAIL),
            _make_tc("TC-3", verdict=ExecutionVerdict.NEED_REVIEW),
            _make_tc("TC-4", verdict=ExecutionVerdict.BLOCKED),
        ]
        ev = OverallEvaluator()
        result = ev.evaluate(tcs)
        recs = result.review_recommendations
        assert any("TC-2" in r for r in recs)
        assert any("TC-3" in r for r in recs)
        assert not any("TC-1" in r for r in recs)
        assert not any("TC-4" in r for r in recs)
