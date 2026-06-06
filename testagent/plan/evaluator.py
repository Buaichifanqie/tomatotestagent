from __future__ import annotations

import json
import re
from typing import Any

from testagent.plan.models import (
    EvaluationOutput,
    ExecutionStatus,
    ExecutionVerdict,
    FailureType,
    StepExecution,
    TestCase,
)

PER_TC_EVALUATOR_SYSTEM_PROMPT = """你是一个移动端测试评估专家。你需要根据测试用例的执行结果，评估该用例是否通过。
请分析以下测试用例的执行情况，并给出 verdict (PASS/FAIL/BLOCKED/NEED_REVIEW/INCONCLUSIVE)、confidence (0.0-1.0) 和 reason (说明判断依据)。

务必以 JSON 格式输出，格式为:
{
    "verdict": "PASS",
    "confidence": 0.85,
    "reason": "所有步骤执行成功，截图完整",
    "evidence_missing": [],
    "failure_type": null
}
"""


class PerTCEvaluator:
    """Evaluates a single TestCase execution and produces an EvaluationOutput."""

    def __init__(self, llm_provider: Any = None) -> None:
        self._llm = llm_provider

    def evaluate(self, tc: TestCase) -> EvaluationOutput:
        if self._llm:
            return self._llm_evaluate(tc)
        return self._fallback_evaluate(tc)

    def _llm_evaluate(self, tc: TestCase) -> EvaluationOutput:
        prompt = self._build_evaluation_prompt(tc)
        raw = self._llm(prompt)
        return self._parse_llm_response(raw)

    def _fallback_evaluate(self, tc: TestCase) -> EvaluationOutput:
        status = tc.execution.status
        missing = self._find_missing_evidence(tc)

        if status in (ExecutionStatus.FAILED, ExecutionStatus.ABORTED):
            return EvaluationOutput(
                verdict=ExecutionVerdict.FAIL,
                confidence=0.7,
                reason=tc.execution.error_message
                or f"Execution status is {status.value}",
                evidence=list(tc.execution.evidence),
                evidence_missing=missing,
                failure_type=tc.execution.failure_type,
            )

        if status == ExecutionStatus.BLOCKED:
            return EvaluationOutput(
                verdict=ExecutionVerdict.BLOCKED,
                confidence=0.9,
                reason=tc.execution.error_message or "Test case is blocked",
                evidence=list(tc.execution.evidence),
                evidence_missing=missing,
            )

        if status == ExecutionStatus.EXECUTED:
            # Check for assert warnings — downgrade to NEED_REVIEW if present
            if tc.execution.assert_warnings:
                warn_summary = "; ".join(tc.execution.assert_warnings[:3])
                return EvaluationOutput(
                    verdict=ExecutionVerdict.NEED_REVIEW,
                    confidence=0.5,
                    reason=f"All steps executed but {len(tc.execution.assert_warnings)} assert warning(s): {warn_summary}",
                    evidence=list(tc.execution.evidence),
                    evidence_missing=missing,
                )
            if missing:
                return EvaluationOutput(
                    verdict=ExecutionVerdict.PASS,
                    confidence=0.6,
                    reason="All steps executed but some evidence is missing",
                    evidence=list(tc.execution.evidence),
                    evidence_missing=missing,
                )
            return EvaluationOutput(
                verdict=ExecutionVerdict.PASS,
                confidence=0.85,
                reason="All steps executed successfully with complete evidence",
                evidence=list(tc.execution.evidence),
                evidence_missing=missing,
            )

        return EvaluationOutput(
            verdict=ExecutionVerdict.INCONCLUSIVE,
            confidence=0.3,
            reason=f"Test case is in {status.value} state, cannot evaluate definitively",
            evidence=list(tc.execution.evidence),
            evidence_missing=missing,
        )

    def _find_missing_evidence(self, tc: TestCase) -> list[str]:
        missing: list[str] = []
        for step in tc.execution.steps:
            if not step.screenshot_after:
                missing.append(
                    f"Step {step.step} ({step.action} on '{step.target}'): missing screenshot_after"
                )
        return missing

    def _build_evaluation_prompt(self, tc: TestCase) -> str:
        lines: list[str] = []
        lines.append(f"Test Case ID: {tc.id}")
        lines.append(f"Title: {tc.title}")
        lines.append(f"Priority: {tc.priority}")
        lines.append(f"Execution Status: {tc.execution.status.value}")
        if tc.execution.error_message:
            lines.append(f"Error: {tc.execution.error_message}")
        if tc.execution.failure_type:
            lines.append(f"Failure Type: {tc.execution.failure_type.value}")
        lines.append("")
        lines.append("Steps:")
        for step in tc.execution.steps:
            snap = "has screenshot" if step.screenshot_after else "NO screenshot"
            lines.append(
                f"  Step {step.step}: {step.action} on '{step.target}' -> "
                f"{'PASS' if step.success else 'FAIL'} ({snap})"
            )
        return "\n".join(lines)

    def _parse_llm_response(self, raw: str) -> EvaluationOutput:
        json_str = self._extract_json(raw)
        if not json_str:
            return EvaluationOutput(
                verdict=ExecutionVerdict.NEED_REVIEW,
                confidence=0.0,
                reason="Failed to parse LLM response as JSON",
                evaluation_notes=f"Raw response: {raw[:500]}",
            )
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError:
            return EvaluationOutput(
                verdict=ExecutionVerdict.NEED_REVIEW,
                confidence=0.0,
                reason="Invalid JSON in LLM response",
                evaluation_notes=f"Raw response: {raw[:500]}",
            )

        try:
            verdict_str = data.get("verdict", "NEED_REVIEW")
            verdict = ExecutionVerdict(verdict_str)
        except ValueError:
            verdict = ExecutionVerdict.NEED_REVIEW

        confidence = float(data.get("confidence", 0.0))
        confidence = max(0.0, min(1.0, confidence))

        failure_type_raw = data.get("failure_type")
        failure_type: FailureType | None = None
        if failure_type_raw:
            try:
                failure_type = FailureType(failure_type_raw)
            except ValueError:
                pass

        return EvaluationOutput(
            verdict=verdict,
            confidence=confidence,
            reason=data.get("reason", ""),
            evidence_missing=data.get("evidence_missing", []),
            failure_type=failure_type,
        )

    @staticmethod
    def _extract_json(raw: str) -> str:
        """Extract JSON from markdown-fenced code block or bare text."""
        pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
        match = re.search(pattern, raw, re.DOTALL)
        if match:
            return match.group(1).strip()
        # Try the whole string as JSON
        stripped = raw.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            return stripped
        return ""
