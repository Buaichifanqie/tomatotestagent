"""Log digest generator for CaseJudgeAgent.

Compresses verbose execution logs into a concise event flow
for the LLM Judge to consume.
"""
from __future__ import annotations

import re
from typing import Any

from testagent.plan.models import StepExecution, TestCase


def generate_log_digest(tc: TestCase) -> str:
    """Generate a concise log digest from a test case's execution data.

    Extracts key events from StepExecution records and filters out noise.
    """
    lines: list[str] = []

    for step_exec in tc.execution.steps:
        line = _format_step(step_exec)
        if line:
            lines.append(line)

    # Add assert warnings
    for warning in tc.execution.assert_warnings:
        lines.append(f"[ASSERT WARNING] {warning}")

    # Add error summary if failed
    if tc.execution.error_message:
        lines.append(f"[ERROR] {tc.execution.error_message}")

    return "\n".join(lines) if lines else "(No execution steps recorded)"


def _format_step(step: StepExecution) -> str:
    """Format a single step execution into a digest line."""
    status = "✓" if step.success else "✗"
    action = step.action or "?"
    target = step.target or ""
    duration = f" ({step.duration_ms}ms)" if step.duration_ms else ""

    # Build the core line
    parts = [f"[{step.step}] {action}"]
    if target:
        parts.append(target)
    parts.append(f"{status}{duration}")

    line = " ".join(parts)

    # Append warning if present
    if step.warning:
        line += f" ⚠ {step.warning}"

    # Append error if failed
    if step.error_message and not step.success:
        line += f" — {step.error_message[:100]}"

    return line


def generate_steps_description(tc: TestCase) -> str:
    """Generate a formatted steps description for the judge prompt.

    Includes both the expected steps and their execution results.
    """
    lines: list[str] = []

    for step in tc.steps:
        expected = step.expected or "(无明确期望)"
        action_desc = f"{step.action}"
        if step.target:
            action_desc += f" {step.target}"
        if step.value:
            action_desc += f" value={step.value}"
        if step.tap_first:
            action_desc += f" (tap_first: {step.tap_first})"

        lines.append(f"  {step.step}. {action_desc} → 期望: {expected}")

    return "\n".join(lines)
