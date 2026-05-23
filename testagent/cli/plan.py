from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path

import typer

from testagent.plan.execution_engine import ExecutionEngine
from testagent.plan.evaluator import PerTCEvaluator
from testagent.plan.models import PlanConfig, TestCase
from testagent.plan.overall_evaluator import OverallEvaluator
from testagent.plan.prd_parser import PrdParser
from testagent.plan.report_generator import ReportGenerator
from testagent.plan.test_case_generator import TestCaseGenerator


# ── helper functions ─────────────────────────────────────────────────────────


def parse_requirement(requirement: str) -> tuple[str, bool]:
    """Determine if input is a file path or raw text.

    Returns:
        A tuple of (content, is_file_path). If ``requirement`` points to an
        existing file, ``content`` is the path string and ``is_file_path`` is
        ``True``. Otherwise ``content`` is the original text and ``is_file_path``
        is ``False``.
    """
    path = Path(requirement)
    if path.exists() and path.is_file():
        return requirement, True
    return requirement, False


def _sanitize_name(name: str) -> str:
    """Sanitize a string for use as a directory name component."""
    safe = re.sub(r"[\s_]+", "-", name)
    safe = re.sub(r"[^a-zA-Z0-9\-.]", "", safe)
    safe = re.sub(r"-{2,}", "-", safe)
    safe = safe.strip("-")
    return safe or "plan"


def setup_output_dir(plan_name: str, base_dir: str = "") -> str:
    """Create and return the output directory path.

    The directory is created at ``{base_dir}/{YYYY-MM-DD-HHMMSS}-{safe_name}/``.

    Args:
        plan_name: The human-readable plan name used for the directory suffix.
        base_dir: Parent directory. Defaults to ``os.getcwd()/reports``.

    Returns:
        Absolute path to the created output directory.
    """
    if not base_dir:
        base_dir = str(Path.cwd() / "reports")

    safe_name = _sanitize_name(plan_name)
    timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
    dir_name = f"{timestamp}-{safe_name}"
    output_dir = Path(base_dir) / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)
    return str(output_dir)


def format_tc_summary(test_cases: list[TestCase]) -> str:
    """Format a human-readable summary of generated test cases.

    Args:
        test_cases: The list of ``TestCase`` objects to summarise.

    Returns:
        A multi-line string suitable for display, or an empty string when the
        list is empty.
    """
    if not test_cases:
        return ""

    lines = ["Generated Test Cases:", ""]
    for tc in test_cases:
        priority_tag = f"[{tc.priority}]"
        core_tag = " [CORE]" if tc.is_core else ""
        lines.append(f"  {tc.id}: {tc.title} {priority_tag}{core_tag}")

    lines.append("")
    lines.append(f"Total: {len(test_cases)} test case(s)")
    return "\n".join(lines)


def present_tc_to_user(test_cases: list[TestCase], auto_yes: bool) -> bool:
    """Present test cases to the user for confirmation.

    Args:
        test_cases: The list of generated ``TestCase`` objects.
        auto_yes: When ``True``, always return ``True`` without prompting.

    Returns:
        ``True`` when the user confirms (or ``auto_yes`` is set), ``False``
        when the user rejects or the list is empty.
    """
    if auto_yes:
        return True

    if not test_cases:
        typer.echo("No test cases generated.")
        return False

    summary = format_tc_summary(test_cases)
    typer.echo(summary)
    return typer.confirm("Proceed with execution?")


# ── main orchestration ───────────────────────────────────────────────────────


def plan_command(
    requirement: str,
    name: str = "",
    app_package: str = "",
    app_activity: str = "",
    auto_yes: bool = False,
) -> str | None:
    """Main orchestration function called by the Typer ``plan`` command.

    Orchestrates the full plan lifecycle:

    0.  Parse input (file path vs. raw text).
    1.  Parse PRD document (if the input is a file).
    2.  Generate test cases via ``TestCaseGenerator``.
    3.  Present test cases to the user for confirmation.
    4.  Execute all test cases via ``ExecutionEngine``.
    5.  Per-test-case evaluation via ``PerTCEvaluator``.
    6.  Overall evaluation via ``OverallEvaluator`` and report generation via
        ``ReportGenerator``.

    Args:
        requirement: A product requirement document path or a natural-language
            requirement description.
        name: Optional custom plan name. If empty, derived from the file stem
            (when requirement is a file) or ``"adhoc-plan"``.
        app_package: Android app package name.
        app_activity: Android app launch activity.
        auto_yes: Skip the user confirmation step.

    Returns:
        The absolute path to the generated Markdown report, or ``None`` if the
        pipeline was aborted (no test cases generated, or user cancelled).
    """
    # ── Phase 0: Parse input ────────────────────────────────────────────────
    content, is_file = parse_requirement(requirement)
    typer.echo(f"Input: {'file' if is_file else 'raw text'} ({len(content)} chars)")

    # Determine plan name
    if not name:
        if is_file:
            name = Path(content).stem
        else:
            name = "adhoc-plan"

    # ── Phase 1: Parse PRD (if file) ────────────────────────────────────────
    if is_file:
        typer.echo("Parsing PRD document...")
        parser = PrdParser()
        prd_doc = parser.parse(content)
        prd_text = prd_doc.formatted_text
    else:
        prd_text = content

    # ── Set up output directory ─────────────────────────────────────────────
    output_dir = setup_output_dir(name)
    typer.echo(f"Output directory: {output_dir}")

    config = PlanConfig(
        name=name,
        app_package=app_package,
        app_activity=app_activity,
        output_dir=output_dir,
        auto_yes=auto_yes,
    )

    # ── Phase 2: Generate test cases ────────────────────────────────────────
    typer.echo("Generating test cases...")
    ts_gen = TestCaseGenerator()
    test_cases = ts_gen.generate(prd_text, plan_name=name)

    if not test_cases:
        typer.echo("No test cases generated. Aborting.")
        return None

    typer.echo(f"Generated {len(test_cases)} test case(s).")

    # ── Phase 3: Present to user ────────────────────────────────────────────
    if not present_tc_to_user(test_cases, auto_yes=auto_yes):
        typer.echo("Execution cancelled by user.")
        return None

    # ── Phase 4: Execute all TCs ────────────────────────────────────────────
    typer.echo("Executing test cases...")
    engine = ExecutionEngine(config)
    executed_tcs = engine.execute_all(test_cases)

    # ── Phase 5: Per-TC evaluation ──────────────────────────────────────────
    typer.echo("Evaluating test case results...")
    evaluator = PerTCEvaluator()
    for tc in executed_tcs:
        evaluation = evaluator.evaluate(tc)
        tc.execution.verdict = evaluation.verdict
        tc.execution.confidence = evaluation.confidence
        tc.execution.reason = evaluation.reason

    # ── Phase 6: Overall evaluation + report generation ─────────────────────
    typer.echo("Generating overall evaluation and report...")
    overall_evaluator = OverallEvaluator()
    overall = overall_evaluator.evaluate(executed_tcs)

    report_gen = ReportGenerator(output_dir)
    report_path = report_gen.generate(name, executed_tcs, overall, config)

    typer.echo(f"Report generated: {report_path}")
    return report_path
