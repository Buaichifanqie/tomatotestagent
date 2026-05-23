from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from testagent.plan.evaluator import PER_TC_EVALUATOR_SYSTEM_PROMPT, PerTCEvaluator
from testagent.plan.models import (
    EvaluationOutput,
    ExecutionStatus,
    ExecutionVerdict,
    FailureType,
    StepExecution,
    TCExecution,
    TestCase,
)


# ── helpers ──────────────────────────────────────────────────────────────────


def _make_tc(
    status: ExecutionStatus = ExecutionStatus.EXECUTED,
    steps: list[StepExecution] | None = None,
    error_message: str = "",
    failure_type: FailureType | None = None,
    evidence_missing: list[str] | None = None,
) -> TestCase:
    execution = TCExecution(
        status=status,
        steps=steps or [],
        error_message=error_message,
        failure_type=failure_type,
        evidence_missing=evidence_missing or [],
    )
    return TestCase(
        id="TC-001",
        title="Sample test case",
        execution=execution,
    )


def _make_step(
    step: int,
    action: str = "click",
    target: str = "button",
    success: bool = True,
    screenshot_after: str = "screenshot.png",
) -> StepExecution:
    return StepExecution(
        step=step,
        action=action,
        target=target,
        success=success,
        screenshot_after=screenshot_after,
    )


# ── tests ────────────────────────────────────────────────────────────────────


class TestInit:
    def test_evaluator_init(self) -> None:
        """Can create a PerTCEvaluator with and without an llm_provider."""
        ev = PerTCEvaluator()
        assert ev._llm is None

        dummy = MagicMock()
        ev2 = PerTCEvaluator(llm_provider=dummy)
        assert ev2._llm is dummy


class TestFallbackEvaluate:
    def test_fallback_evaluate_all_passed(self) -> None:
        """EXECUTED with all steps passing -> PASS verdict."""
        steps = [
            _make_step(1, action="click", target="login_btn"),
            _make_step(2, action="input", target="username"),
        ]
        tc = _make_tc(status=ExecutionStatus.EXECUTED, steps=steps)
        ev = PerTCEvaluator()
        result = ev._fallback_evaluate(tc)
        assert isinstance(result, EvaluationOutput)
        assert result.verdict == ExecutionVerdict.PASS
        assert result.confidence == 0.85
        assert "complete evidence" in result.reason

    def test_fallback_evaluate_failed(self) -> None:
        """FAILED with assertion_error -> FAIL verdict."""
        tc = _make_tc(
            status=ExecutionStatus.FAILED,
            failure_type=FailureType.ASSERTION_FAILED,
            error_message="Expected element not found",
        )
        ev = PerTCEvaluator()
        result = ev._fallback_evaluate(tc)
        assert result.verdict == ExecutionVerdict.FAIL
        assert result.confidence == 0.7
        assert result.failure_type == FailureType.ASSERTION_FAILED
        assert "Expected element not found" in result.reason

    def test_fallback_evaluate_blocked(self) -> None:
        """BLOCKED -> BLOCKED verdict."""
        tc = _make_tc(
            status=ExecutionStatus.BLOCKED,
            error_message="Precondition failed: app not installed",
        )
        ev = PerTCEvaluator()
        result = ev._fallback_evaluate(tc)
        assert result.verdict == ExecutionVerdict.BLOCKED
        assert result.confidence == 0.9
        assert "app not installed" in result.reason

    def test_fallback_evaluate_missing_evidence(self) -> None:
        """EXECUTED with steps missing screenshots -> PASS with lower confidence."""
        steps = [
            _make_step(1, success=True, screenshot_after=""),
            _make_step(2, success=True, screenshot_after="img.png"),
        ]
        tc = _make_tc(status=ExecutionStatus.EXECUTED, steps=steps)
        ev = PerTCEvaluator()
        result = ev._fallback_evaluate(tc)
        assert result.verdict == ExecutionVerdict.PASS
        assert result.confidence == 0.6
        assert len(result.evidence_missing) == 1
        assert "Step 1" in result.evidence_missing[0]


class TestEvaluateWithLLM:
    def test_evaluate_with_llm(self) -> None:
        """When llm_provider is set, _llm_evaluate is called."""
        dummy_provider = MagicMock(return_value='{"verdict": "PASS", "confidence": 0.9, "reason": "ok"}')
        tc = _make_tc()
        ev = PerTCEvaluator(llm_provider=dummy_provider)
        result = ev.evaluate(tc)
        dummy_provider.assert_called_once()
        assert isinstance(result, EvaluationOutput)
        assert result.verdict == ExecutionVerdict.PASS
        assert result.confidence == 0.9


class TestParseLlmResponse:
    def test_parse_llm_response(self) -> None:
        """Valid JSON response -> EvaluationOutput."""
        ev = PerTCEvaluator()
        raw = json.dumps(
            {
                "verdict": "FAIL",
                "confidence": 0.95,
                "reason": "Step 2 failed: element not found",
                "evidence_missing": ["Step 2 screenshot missing"],
                "failure_type": "ASSERTION_FAILED",
            }
        )
        result = ev._parse_llm_response(raw)
        assert result.verdict == ExecutionVerdict.FAIL
        assert result.confidence == 0.95
        assert "element not found" in result.reason
        assert result.failure_type == FailureType.ASSERTION_FAILED
        assert "Step 2 screenshot missing" in result.evidence_missing

    def test_parse_llm_response_invalid(self) -> None:
        """Invalid JSON -> NEED_REVIEW verdict."""
        ev = PerTCEvaluator()
        result = ev._parse_llm_response("not valid json at all")
        assert result.verdict == ExecutionVerdict.NEED_REVIEW
        assert result.confidence == 0.0
        assert "Failed to parse" in result.reason


class TestFindMissingEvidence:
    def test_find_missing_evidence(self) -> None:
        """Steps without screenshots are reported as missing evidence."""
        steps = [
            _make_step(1, screenshot_after=""),
            _make_step(2, screenshot_after="img.png"),
            _make_step(3, screenshot_after=""),
        ]
        tc = _make_tc(steps=steps)
        ev = PerTCEvaluator()
        missing = ev._find_missing_evidence(tc)
        assert len(missing) == 2
        assert all("missing screenshot_after" in m for m in missing)

    def test_find_missing_evidence_none(self) -> None:
        """All steps with screenshots -> empty list."""
        steps = [
            _make_step(1, screenshot_after="img1.png"),
            _make_step(2, screenshot_after="img2.png"),
        ]
        tc = _make_tc(steps=steps)
        ev = PerTCEvaluator()
        missing = ev._find_missing_evidence(tc)
        assert missing == []


class TestSystemPrompt:
    def test_system_prompt_defined(self) -> None:
        """PER_TC_EVALUATOR_SYSTEM_PROMPT is a non-empty string."""
        assert isinstance(PER_TC_EVALUATOR_SYSTEM_PROMPT, str)
        assert len(PER_TC_EVALUATOR_SYSTEM_PROMPT) > 0
