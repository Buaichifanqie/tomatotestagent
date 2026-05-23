from __future__ import annotations

from typing import Any

from testagent.plan.models import (
    ExecutionVerdict,
    OverallEvaluation,
    TestCase,
)


class OverallEvaluator:
    """Evaluates a list of TestCase executions and produces an OverallEvaluation."""

    def __init__(self, llm_provider: Any = None) -> None:
        self._llm = llm_provider

    def evaluate(self, test_cases: list[TestCase]) -> OverallEvaluation:
        if not test_cases:
            return OverallEvaluation(
                verdict=ExecutionVerdict.INCONCLUSIVE,
                total_count=0,
                passed_count=0,
                core_total=0,
                core_passed=0,
                need_review_count=0,
                blocked_count=0,
                summary="No test cases to evaluate",
                review_recommendations=[],
            )

        total = len(test_cases)
        passed = sum(
            1
            for tc in test_cases
            if tc.execution.verdict == ExecutionVerdict.PASS
        )
        core_total = sum(1 for tc in test_cases if tc.is_core)
        core_passed = sum(
            1
            for tc in test_cases
            if tc.is_core and tc.execution.verdict == ExecutionVerdict.PASS
        )
        blocked = sum(
            1
            for tc in test_cases
            if tc.execution.verdict == ExecutionVerdict.BLOCKED
        )
        need_review = sum(
            1
            for tc in test_cases
            if tc.execution.verdict == ExecutionVerdict.NEED_REVIEW
        )

        # Categorise for rule matching
        core_fails = [
            tc
            for tc in test_cases
            if tc.is_core and tc.execution.verdict == ExecutionVerdict.FAIL
        ]
        all_pass = all(
            tc.execution.verdict == ExecutionVerdict.PASS for tc in test_cases
        )
        mostly_blocked = total > 0 and (blocked / total) >= 0.5
        non_core_fails = [
            tc
            for tc in test_cases
            if not tc.is_core and tc.execution.verdict == ExecutionVerdict.FAIL
        ]
        core_all_pass = core_total > 0 and core_passed == core_total

        # Verdict rules (in priority order)
        if core_fails:
            verdict = ExecutionVerdict.FAIL
        elif all_pass:
            verdict = ExecutionVerdict.PASS
        elif mostly_blocked:
            verdict = ExecutionVerdict.INCONCLUSIVE
        elif core_all_pass and non_core_fails:
            verdict = ExecutionVerdict.PARTIAL
        else:
            verdict = ExecutionVerdict.FAIL

        review_recommendations = [
            f"{tc.id} ({tc.execution.verdict.value})"
            for tc in test_cases
            if tc.execution.verdict
            in (ExecutionVerdict.FAIL, ExecutionVerdict.NEED_REVIEW)
        ]

        summary = self._build_summary(
            verdict, core_total, core_passed, total, passed
        )

        return OverallEvaluation(
            verdict=verdict,
            total_count=total,
            passed_count=passed,
            core_total=core_total,
            core_passed=core_passed,
            need_review_count=need_review,
            blocked_count=blocked,
            summary=summary,
            review_recommendations=review_recommendations,
        )

    @staticmethod
    def _build_summary(
        verdict: ExecutionVerdict,
        core_total: int,
        core_passed: int,
        total: int,
        passed: int,
    ) -> str:
        core_str = (
            f" | Core {core_passed}/{core_total}" if core_total > 0 else ""
        )
        return f"Overall: {verdict.value} | Passed {passed}/{total}{core_str}"
