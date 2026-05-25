from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import typer

from testagent.plan.execution_engine import ExecutionEngine
from testagent.plan.evaluator import PerTCEvaluator
from testagent.plan.models import PlanConfig, TestCase
from testagent.plan.overall_evaluator import OverallEvaluator
from testagent.plan.prd_parser import PrdParser
from testagent.plan.report_generator import ReportGenerator
from testagent.plan.test_case_generator import TestCaseGenerator


# ── helper functions ─────────────────────────────────────────────────────────


def _detect_app_package(requirement: str) -> str | None:
    """Auto-detect app package from connected Android device.

    Uses ``adb`` to list third-party packages on the connected device, then
    asks the LLM to match the app description (from the requirement text) to
    one of the installed packages.

    Returns:
        The matched package name, or ``None`` if detection fails.
    """
    import subprocess

    # ── Check device connection ────────────────────────────────────────────
    try:
        result = subprocess.run(
            ["adb", "devices"],
            capture_output=True, text=True, timeout=5,
        )
        lines = [l.strip() for l in result.stdout.split("\n") if l.strip()]
        # lines[0] is "List of devices attached"; anything after with \t means connected
        devices = [l for l in lines[1:] if "\tdevice" in l]
        if not devices:
            typer.echo("  [adb: no device connected, cannot auto-detect app package]")
            return None
    except FileNotFoundError:
        typer.echo("  [adb not found, cannot auto-detect app package]")
        return None
    except Exception as exc:
        typer.echo(f"  [adb check failed: {exc}]")
        return None

    # ── List 3rd-party packages ────────────────────────────────────────────
    try:
        result = subprocess.run(
            ["adb", "shell", "pm", "list", "packages", "-3"],
            capture_output=True, text=True, timeout=10,
        )
        packages = [
            line.replace("package:", "").strip()
            for line in result.stdout.split("\n")
            if line.startswith("package:")
        ]
        if not packages:
            typer.echo("  [no third-party packages found on device]")
            return None
    except Exception as exc:
        typer.echo(f"  [failed to list packages: {exc}]")
        return None

    # ── Use LLM to match ───────────────────────────────────────────────────
    from testagent.config.settings import get_settings
    from testagent.llm.local_provider import LLMProviderFactory

    settings = get_settings()
    provider = LLMProviderFactory.create(settings)

    package_list = "\n".join(f"  {i+1}. {p}" for i, p in enumerate(packages))
    prompt = (
        f"用户需求: {requirement}\n\n"
        f"设备上已安装的第三方应用包名列表:\n{package_list}\n\n"
        "请根据用户需求，选择最匹配的应用包名。只输出包名本身，不要任何额外文字。"
    )

    try:
        response = asyncio.run(provider.chat(
            system="你是一个 Android 工程师，擅长根据应用名称匹配包名。",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        ))
        for block in response.content:
            if block.get("type") == "text":
                matched = str(block.get("text", "")).strip()
                # Validate the match is in the installed list
                if matched in packages:
                    typer.echo(f"  [auto-detected app package: {matched}]")
                    return matched
                else:
                    typer.echo(
                        f"  [LLM suggested '{matched}' but it's not in the installed list]"
                    )
                    return None
    except Exception as exc:
        typer.echo(f"  [LLM matching failed: {exc}]")
        return None

    return None


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


def _try_init_vision_client() -> Any | None:
    """Try to initialize a synchronous vision client for image description.

    Returns a wrapper with a ``describe(image_path: str) -> str`` method,
    or ``None`` if vision is not configured/available.
    """
    try:
        import asyncio
        import base64

        from testagent.config.settings import get_settings

        settings = get_settings()
        vision_key = settings.vision_api_key.get_secret_value()
        if not vision_key:
            return None

        from testagent.mcp_servers.vision_server.volcano_client import (
            VolcanoVisionClient,
        )

        async_client = VolcanoVisionClient(
            api_key=vision_key,
            api_url=settings.vision_api_url,
            model=settings.vision_model,
            timeout=settings.vision_timeout,
            max_retries=settings.vision_max_retries,
        )

        class _SyncVisionAdapter:
            """Synchronous adapter wrapping an async vision client."""

            def __init__(self, client: VolcanoVisionClient) -> None:
                self._client = client

            def describe(self, image_path: str) -> str:
                with open(image_path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("ascii")
                result = asyncio.run(
                    self._client.analyze(
                        b64, "请详细描述这张图片的内容和布局"
                    )
                )
                if "error" in result:
                    raise RuntimeError(result["error"])
                return result.get("content", "")

        return _SyncVisionAdapter(async_client)
    except Exception:
        return None


# Known LLM package-name hallucinations (e.g. "buli" instead of "bili").
# The post-processing step in plan_command replaces these with the real
# app_package wherever they appear in step targets and values.
_HALLUCINATED_PACKAGES: frozenset[str] = frozenset({
    "tv.danmaku.buli",
})


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

        # ── Phase 1b: Vision image description (optional) ────────────────
        if prd_doc.images:
            vision_client = _try_init_vision_client()
            if vision_client is not None:
                typer.echo(
                    f"  \U0001f50d 正在识别 {len(prd_doc.images)} 张图片..."
                )
                prd_doc.images = parser.describe_images(
                    prd_doc.images, vision_client
                )
                typer.echo("  ✅ 图片描述完成")

        prd_text = prd_doc.formatted_text
    else:
        prd_text = content

    # ── Auto-detect app package if not provided ──────────────────────────
    if not app_package:
        detected = _detect_app_package(requirement)
        if detected:
            app_package = detected

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

    def _build_llm_callable() -> Any:
        """Build a sync callable that wraps the async LLM provider for TC generation."""
        from testagent.config.settings import get_settings
        from testagent.llm.local_provider import LLMProviderFactory

        settings = get_settings()
        provider = LLMProviderFactory.create(settings)

        from testagent.plan.test_case_generator import TC_GENERATION_SYSTEM_PROMPT

        async def _call(text: str) -> str:
            response = await provider.chat(
                system=TC_GENERATION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": text}],
                temperature=0,
            )
            for block in response.content:
                if block.get("type") == "text":
                    return str(block.get("text", ""))
            return ""

        return lambda text: asyncio.run(_call(text))

    # ── Inject app info into TC generation prompt ──────────────────────
    enhanced_prd = prd_text
    app_info_parts = []
    if app_package:
        app_info_parts.append(f"Android app package name: {app_package}")
    if app_activity:
        app_info_parts.append(f"Android launch activity: {app_activity}")
    if app_info_parts:
        enhanced_prd += "\n\n" + "\n".join(app_info_parts)

    # ── Phase 1c: UI pre-scan (optional enhancement) ──────────────────
    if app_package:
        typer.echo("  Scanning real UI elements for TC generation...")
        try:
            from testagent.plan.ui_scanner import (
                discover_ui_elements,
                format_ui_context,
            )

            scan_result = discover_ui_elements(
                package=app_package,
                activity=app_activity,
            )
            if scan_result and scan_result.elements:
                ui_context = format_ui_context(scan_result)
                enhanced_prd += ui_context
                typer.echo(
                    f"  Discovered {len(scan_result.elements)} UI elements "
                    f"({scan_result.scan_duration_ms}ms)"
                )
            else:
                typer.echo("  [UI pre-scan returned no elements — "
                           "continuing with default prompt]")
        except Exception as exc:
            typer.echo(
                f"  [UI pre-scan failed: {exc} — "
                f"falling back to default prompt]"
            )
        finally:
            # Kill the app after pre-scan so TCs start from a clean state
            import subprocess
            try:
                subprocess.run(
                    ["adb", "shell", "am", "force-stop", app_package],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass

    ts_gen = TestCaseGenerator(llm_provider=_build_llm_callable())
    test_cases = ts_gen.generate(enhanced_prd, plan_name=name)

    if not test_cases:
        typer.echo("No test cases generated. Aborting.")
        return None

    typer.echo(f"Generated {len(test_cases)} test case(s).")

    # ── Fix LLM-hallucinated package names in generated TCs ─────────────────
    # The LLM often generates "tv.danmaku.buli" instead of "tv.danmaku.bili".
    # Override launch targets with the correct package and fix exec commands.
    if app_package:
        for tc in test_cases:
            for step in tc.steps:
                if step.action == "launch":
                    step.target = app_package
                else:
                    for wrong in _HALLUCINATED_PACKAGES:
                        if wrong != app_package:
                            step.target = step.target.replace(wrong, app_package)
                            step.value = step.value.replace(wrong, app_package)

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
