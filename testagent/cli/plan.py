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
from testagent.plan.models import PlanConfig, TestCase, TestStep
from testagent.plan.overall_evaluator import OverallEvaluator
from testagent.plan.prd_parser import PrdParser
from testagent.plan.report_generator import ReportGenerator
from testagent.plan.test_case_generator import TestCaseGenerator
from testagent.rag.app_memory import (
    format_retrieved_cases_for_prompt,
    serialize_cases_for_storage,
)


# ── helper functions ─────────────────────────────────────────────────────────


async def _detect_app_package(requirement: str) -> str | None:
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
        response = await provider.chat(
            system="你是一个 Android 工程师，擅长根据应用名称匹配包名。",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
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


async def _describe_images_with_vision(
    images: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Describe images using the async vision client (no asyncio.run nesting).

    Each image in the list is sent to the vision API in sequence. On
    success the description is written back into the dict; on failure
    an error message is stored instead.

    Args:
        images: list of {"path": ..., "description": ...} dicts.

    Returns:
        The same list with descriptions filled in.
    """
    import base64
    from pathlib import Path

    from testagent.config.settings import get_settings

    settings = get_settings()
    vision_key = settings.vision_api_key.get_secret_value()
    if not vision_key:
        for img in images:
            if not img.get("description"):
                img["description"] = "[Vision API not configured]"
        return images

    from testagent.mcp_servers.vision_server.volcano_client import (
        VolcanoVisionClient,
    )

    client = VolcanoVisionClient(
        api_key=vision_key,
        api_url=settings.vision_api_url,
        model=settings.vision_model,
        timeout=settings.vision_timeout,
        max_retries=settings.vision_max_retries,
    )

    for img in images:
        if img.get("description") or not img.get("path"):
            continue
        path = img["path"]
        if not Path(path).exists():
            img["description"] = f"[图片文件不存在: {path}]"
            continue
        try:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            result = await client.analyze(
                b64, "请详细描述这张图片的内容和布局"
            )
            if "error" in result:
                img["description"] = f"[Vision API error: {result['error'][:80]}]"
            else:
                img["description"] = result.get("content", "")[:500]
        except Exception as e:
            img["description"] = f"[图片描述失败: {e}]"

    return images


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
    """Present test cases to the user for confirmation and optional editing.

    Args:
        test_cases: The list of generated ``TestCase`` objects (modified in-place).
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

    while True:
        summary = format_tc_summary(test_cases)
        typer.echo(summary)
        typer.echo("")
        typer.echo("  [y] 执行所有用例  [e] 编辑用例  [n] 取消")
        choice = typer.prompt("  请选择", default="y", show_default=False)

        if choice.lower() == "y":
            return True
        if choice.lower() == "n":
            return False
        if choice.lower() == "e":
            _tc_editor(test_cases)
            continue
        typer.echo("  无效输入，请输入 y / e / n")


def _tc_editor(test_cases: list[TestCase]) -> None:
    """Interactive sub-editor for add / delete / modify test cases."""
    while True:
        typer.echo("")
        typer.echo("  ── 编辑用例 ──")
        typer.echo("  [a] 添加用例  [d] 删除用例  [m] 修改用例  [b] 返回")
        action = typer.prompt("  请选择", default="b", show_default=False)

        if action.lower() == "b":
            return
        if action.lower() == "a":
            _tc_add(test_cases)
        elif action.lower() == "d":
            _tc_delete(test_cases)
        elif action.lower() == "m":
            _tc_modify(test_cases)
        else:
            typer.echo("  无效输入，请输入 a / d / m / b")


def _tc_add(test_cases: list[TestCase]) -> None:
    """Add test cases interactively, supports batch adding."""
    typer.echo("")
    typer.echo("  ── 添加新用例（添加完一个后可继续添加）──")
    while True:
        tc_id = typer.prompt("  用例 ID（如 TC-NEW-001）")
        title = typer.prompt("  用例标题")
        priority = typer.prompt("  优先级", default="P1")
        is_core = typer.confirm("  是否为核心用例", default=False)
        requirement_ids_str = typer.prompt("  关联需求 ID（逗号分隔，留空跳过）", default="")
        requirement_ids = [r.strip() for r in requirement_ids_str.split(",") if r.strip()] if requirement_ids_str else []

        steps: list[TestStep] = []
        typer.echo("  步骤（action: tap / type / swipe / assert / launch / exec / screenshot / wait，输入空行结束）")
        step_num = 1
        while True:
            action = typer.prompt(f"    步骤{step_num} action", default="")
            if not action:
                break
            target = typer.prompt(f"    步骤{step_num} target", default="")
            value = typer.prompt(f"    步骤{step_num} value", default="")
            steps.append(TestStep(step=step_num, action=action, target=target, value=value))
            step_num += 1

        new_tc = TestCase(
            id=tc_id,
            title=title,
            priority=priority,
            is_core=is_core,
            requirement_ids=requirement_ids,
            steps=steps,
        )
        test_cases.append(new_tc)
        typer.echo(f"  ✅ 已添加: {tc_id} {title}")

        if not typer.confirm("  继续添加下一个用例?", default=False):
            break
        typer.echo("")


def _tc_delete(test_cases: list[TestCase]) -> None:
    """Delete test cases by index, supports batch input like '1,3,5'."""
    if not test_cases:
        typer.echo("  没有可删除的用例")
        return
    typer.echo("")
    typer.echo("  ── 删除用例 ──")
    for i, tc in enumerate(test_cases):
        typer.echo(f"    {i+1}. {tc.id}: {tc.title}")
    typer.echo("  支持批量删除，用逗号或空格分隔，如: 1,3,5 或 1 3 5")
    idx_str = typer.prompt("  输入要删除的编号")
    # Parse: split by comma, space, or both
    tokens = re.split(r"[,\s]+", idx_str.strip())
    indices: list[int] = []
    for t in tokens:
        try:
            idx = int(t) - 1
        except ValueError:
            typer.echo(f"  无效编号: {t}")
            return
        if not (0 <= idx < len(test_cases)):
            typer.echo(f"  编号 {t} 超出范围")
            return
        indices.append(idx)
    # Delete from highest index to lowest to avoid shifting
    for idx in sorted(set(indices), reverse=True):
        removed = test_cases.pop(idx)
        typer.echo(f"  ✅ 已删除: {removed.id} {removed.title}")


def _tc_modify(test_cases: list[TestCase]) -> None:
    """Modify test cases interactively, supports batch modifying."""
    if not test_cases:
        typer.echo("  没有可修改的用例")
        return
    typer.echo("")
    typer.echo("  ── 修改用例（修改完一个后可继续修改下一个）──")
    while True:
        for i, tc in enumerate(test_cases):
            typer.echo(f"    {i+1}. {tc.id}: {tc.title}")
        idx_str = typer.prompt("  输入要修改的编号（输入 b 返回）", default="b", show_default=False)
        if idx_str.lower() == "b":
            return
        try:
            idx = int(idx_str) - 1
        except ValueError:
            typer.echo("  无效编号")
            continue
        if not (0 <= idx < len(test_cases)):
            typer.echo("  编号超出范围")
            continue

        tc = test_cases[idx]
        typer.echo(f"\n  当前: {tc.id} {tc.title} [{tc.priority}] {'[CORE]' if tc.is_core else ''}")
        typer.echo("  (t) 改标题  (p) 改优先级  (c) 改核心标记  (s) 改步骤  (b) 返回")
        field = typer.prompt("  选择", default="b", show_default=False)

        if field.lower() == "t":
            new_title = typer.prompt("  新标题", default=tc.title)
            tc.title = new_title
            typer.echo(f"  ✅ 标题已更新: {new_title}")
        elif field.lower() == "p":
            new_p = typer.prompt("  新优先级", default=tc.priority)
            tc.priority = new_p
            typer.echo(f"  ✅ 优先级已更新: {new_p}")
        elif field.lower() == "c":
            tc.is_core = not tc.is_core
            typer.echo(f"  ✅ 核心标记已更新: {'是' if tc.is_core else '否'}")
        elif field.lower() == "s":
            _tc_edit_steps(tc)
        elif field.lower() == "b":
            continue

        if not typer.confirm("  继续修改下一个用例?", default=False):
            return
        typer.echo("")


def _tc_edit_steps(tc: TestCase) -> None:
    """Edit steps of a test case interactively."""
    typer.echo(f"\n  当前步骤 ({tc.id}):")
    if tc.steps:
        for s in tc.steps:
            typer.echo(f"    {s.step}. [{s.action}] target={s.target} value={s.value}")
    else:
        typer.echo("    （无步骤）")

    typer.echo("")
    typer.echo("  [a] 添加步骤  [d] 删除步骤  [b] 返回")
    action = typer.prompt("  请选择", default="b", show_default=False)

    if action.lower() == "a":
        step_num = len(tc.steps) + 1
        a = typer.prompt(f"    步骤{step_num} action (tap/type/swipe/assert/launch/exec/screenshot/wait)")
        target = typer.prompt(f"    步骤{step_num} target", default="")
        value = typer.prompt(f"    步骤{step_num} value", default="")
        tc.steps.append(TestStep(step=step_num, action=a, target=target, value=value))
        typer.echo(f"  ✅ 已添加步骤 {step_num}")
    elif action.lower() == "d":
        if not tc.steps:
            typer.echo("  没有可删除的步骤")
            return
        idx_str = typer.prompt("  输入要删除的步骤编号")
        try:
            idx = int(idx_str) - 1
        except ValueError:
            typer.echo("  无效编号")
            return
        if 0 <= idx < len(tc.steps):
            removed = tc.steps.pop(idx)
            # 重新编号
            for i, s in enumerate(tc.steps):
                s.step = i + 1
            typer.echo(f"  ✅ 已删除步骤 {removed.step}: [{removed.action}] {removed.target}")
        else:
            typer.echo("  编号超出范围")


# ── main orchestration ───────────────────────────────────────────────────────


def plan_command(
    requirement: str,
    name: str = "",
    app_package: str = "",
    app_activity: str = "",
    app_id: str = "",
    auto_yes: bool = False,
) -> str | None:
    """Main orchestration function — sync entry point for the Typer CLI.

    Wraps the async implementation in ``asyncio.run()``. See
    ``_plan_command_async`` for the full docstring.
    """
    return asyncio.run(_plan_command_async(
        requirement, name=name,
        app_package=app_package, app_activity=app_activity,
        app_id=app_id, auto_yes=auto_yes,
    ))


async def _plan_command_async(
    requirement: str,
    name: str = "",
    app_package: str = "",
    app_activity: str = "",
    app_id: str = "",
    auto_yes: bool = False,
) -> str | None:
    """Async implementation of the full plan lifecycle.

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
        name: Optional custom plan name.
        app_package: Android app package name.
        app_activity: Android app launch activity.
        auto_yes: Skip the user confirmation step.

    Returns:
        The absolute path to the generated Markdown report, or ``None`` if the
        pipeline was aborted.
    """
    # ── Phase 0: Parse input ────────────────────────────────────────────────
    from testagent.db.engine import init_db
    await init_db()

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
            typer.echo(
                f"  \U0001f50d 正在识别 {len(prd_doc.images)} 张图片..."
            )
            prd_doc.images = await _describe_images_with_vision(
                prd_doc.images,
            )
            typer.echo("  ✅ 图片描述完成")

        prd_text = prd_doc.formatted_text
    else:
        prd_text = content

    # ── Auto-detect app package if not provided ──────────────────────────
    if not app_package:
        detected = await _detect_app_package(requirement)
        if detected:
            app_package = detected

    # ── Derive app identifier for App Context Memory ────────────────────
    memory_app_id = app_id or app_package  # explicit --app-id takes priority

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

    # Create LLM provider once — shared between TC generation and execution
    from testagent.config.settings import get_settings
    from testagent.llm.local_provider import LLMProviderFactory

    settings = get_settings()
    llm_provider = LLMProviderFactory.create(settings)

    def _build_llm_callable() -> Any:
        """Build a callable (async) that wraps the shared LLM provider for TC generation."""
        from testagent.plan.test_case_generator import TC_GENERATION_SYSTEM_PROMPT

        async def _call(text: str) -> str:
            response = await llm_provider.chat(
                system=TC_GENERATION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": text}],
                max_tokens=32768,
                temperature=0,
            )
            for block in response.content:
                if block.get("type") == "text":
                    return str(block.get("text", ""))
            return ""

        return _call  # return async callable directly (no asyncio.run wrapper)

    # ── Inject app info into TC generation prompt ──────────────────────
    enhanced_prd = prd_text
    app_info_parts = []
    if app_package:
        app_info_parts.append(f"Android app package name: {app_package}")
    if app_activity:
        app_info_parts.append(f"Android launch activity: {app_activity}")
    if app_info_parts:
        enhanced_prd += "\n\n" + "\n".join(app_info_parts)

    # ── Phase 2.5: Retrieve historical cases from App Context Memory ──────
    history_context = ""
    rag_results = []
    two_stage_results = None
    if memory_app_id:
        try:
            from testagent.rag.factories import create_pipeline
            from testagent.plan.two_stage_retrieval import run_two_stage_retrieval
            from testagent.rag.app_memory import format_learned_patterns_for_prompt

            rag_pipeline = create_pipeline(settings)

            # Two-stage retrieval: broad parallel search + refined dedup query
            two_stage_results = await run_two_stage_retrieval(
                rag_pipeline, enhanced_prd, memory_app_id,
            )

            cases = two_stage_results["cases"]
            patterns = two_stage_results["patterns"]
            doc_results = two_stage_results.get("docs", [])

            # ── Apply decay scoring ──────────────────────────────────────────
            current_version = ""
            try:
                from testagent.db.engine import get_session
                from testagent.db.repository import AppVersionRepository, TestCaseRecordRepository, LearnedPatternRepository
                async with get_session() as session:
                    av_repo = AppVersionRepository(session)
                    av = await av_repo.get_by_app_id(memory_app_id)
                    if av:
                        current_version = av.current_version
            except Exception:
                pass

            if current_version:
                try:
                    from testagent.memory.retrieval_post_processor import apply_decay
                    from datetime import datetime, UTC
                    case_records = {}
                    pattern_records = {}
                    try:
                        async with get_session() as session:
                            tcr_repo = TestCaseRecordRepository(session)
                            lp_repo = LearnedPatternRepository(session)
                            all_case_recs = await tcr_repo.get_by_app_id(memory_app_id, limit=100)
                            case_records = {r.id: r for r in all_case_recs}
                            all_pattern_recs = await lp_repo.get_by_app_id(memory_app_id, limit=100)
                            pattern_records = {r.id: r for r in all_pattern_recs}
                    except Exception:
                        pass
                    all_results = cases + patterns + doc_results
                    all_results = apply_decay(all_results, current_version, datetime.now(UTC), case_records, pattern_records)
                    # Re-split by collection
                    cases = [r for r in all_results if r.metadata.get("collection") == "app_test_cases"]
                    patterns = [r for r in all_results if r.metadata.get("collection") == "app_learned_patterns"]
                    doc_results = [r for r in all_results if r.metadata.get("collection") == "app_documentation"]
                except Exception as exc:
                    typer.echo(f"  [Decay scoring skipped: {exc}]")

            if cases or patterns or doc_results:
                context_parts = []
                if cases:
                    context_parts.append(format_retrieved_cases_for_prompt(cases))
                    typer.echo(f"  Found {len(cases)} historical case(s) from App Context Memory.")
                if patterns:
                    context_parts.append(format_learned_patterns_for_prompt(patterns))
                    typer.echo(f"  Found {len(patterns)} learned pattern(s) from App Context Memory.")
                if doc_results:
                    from testagent.rag.app_memory import format_doc_results_for_prompt
                    context_parts.append(format_doc_results_for_prompt(doc_results))
                    typer.echo(f"  Found {len(doc_results)} document(s) from App Context Memory.")
                history_context = "\n\n".join(context_parts)

                # Flatten for trace recording
                rag_results = cases + patterns + doc_results
            else:
                typer.echo("  No historical cases, patterns, or documents found from App Context Memory.")
        except Exception as exc:
            typer.echo(f"  [App Context Memory two-stage retrieval skipped: {exc}]")
            # Fallback: single-batch retrieval
            try:
                if "rag_pipeline" not in dir():
                    from testagent.rag.factories import create_pipeline
                    rag_pipeline = create_pipeline(settings)
                rag_results = await rag_pipeline.query(
                    query_text=enhanced_prd,
                    collection="app_test_cases",
                    top_k=5,
                    filters={"app_id": memory_app_id},
                )
                if rag_results:
                    history_context = format_retrieved_cases_for_prompt(rag_results)
                    typer.echo(f"  Found {len(rag_results)} historical case(s) from App Context Memory (fallback).")
            except Exception as fallback_exc:
                typer.echo(f"  [App Context Memory fallback retrieval also skipped: {fallback_exc}]")

    # Inject history context before the user's requirement
    if history_context:
        enhanced_prd = history_context + "\n\n" + enhanced_prd

    # ── Record RetrievalTrace ────────────────────────────────────────────
    if memory_app_id and two_stage_results:
        try:
            from testagent.db.engine import get_session
            from testagent.db.repository import RetrievalTraceRepository
            from testagent.models.retrieval_trace import RetrievalTrace as RetrievalTraceModel

            async with get_session() as session:
                repo = RetrievalTraceRepository(session)
                # Record stage 1 trace (cases + patterns + docs before dedup)
                stage1_cases = two_stage_results.get("cases", [])[:3]
                stage1_patterns = two_stage_results.get("patterns", [])[:3]
                stage1_docs = two_stage_results.get("docs", [])[:2]
                stage1_items = [
                    {"id": r.doc_id, "score": r.score, "content_preview": r.content[:200]}
                    for r in stage1_cases + stage1_patterns + stage1_docs
                ]
                await repo.create(RetrievalTraceModel(
                    app_id=memory_app_id,
                    query=prd_text[:2000],
                    query_stage="stage1",
                    retrieved_items=stage1_items,
                    generated_case_ids=[],
                    adoption_score=None,
                ))
                # Record stage 2 trace (additional items after dedup)
                all_cases = two_stage_results.get("cases", [])
                all_patterns = two_stage_results.get("patterns", [])
                all_docs = two_stage_results.get("docs", [])
                stage1_ids = set(two_stage_results.get("stage1_doc_ids", []))
                stage2_items = [
                    {"id": r.doc_id, "score": r.score, "content_preview": r.content[:200]}
                    for r in all_cases + all_patterns + all_docs
                    if r.doc_id not in stage1_ids
                ]
                if stage2_items:
                    await repo.create(RetrievalTraceModel(
                        app_id=memory_app_id,
                        query=prd_text[:2000],
                        query_stage="stage2",
                        retrieved_items=stage2_items,
                        generated_case_ids=[],
                        adoption_score=None,
                    ))
        except Exception as exc:
            typer.echo(f"  [RetrievalTrace save skipped: {exc}]")
    elif memory_app_id and rag_results:
        # Fallback trace for single-batch retrieval
        try:
            from testagent.db.engine import get_session
            from testagent.db.repository import RetrievalTraceRepository
            from testagent.models.retrieval_trace import RetrievalTrace as RetrievalTraceModel

            async with get_session() as session:
                repo = RetrievalTraceRepository(session)
                await repo.create(RetrievalTraceModel(
                    app_id=memory_app_id,
                    query=prd_text[:2000],
                    query_stage="single_batch",
                    retrieved_items=[
                        {"id": r.doc_id, "score": r.score, "content_preview": r.content[:200]}
                        for r in rag_results
                    ],
                    generated_case_ids=[],
                    adoption_score=None,
                ))
        except Exception as exc:
            typer.echo(f"  [RetrievalTrace save skipped: {exc}]")

    ts_gen = TestCaseGenerator(llm_provider=_build_llm_callable())
    test_cases = await ts_gen.generate(enhanced_prd, plan_name=name)

    if not test_cases:
        typer.echo("No test cases generated. Aborting.")
        raw = ts_gen.last_raw_output
        if raw:
            typer.echo("\n--- Raw LLM output (first 2000 chars) ---")
            typer.echo(raw[:2000])
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
    original_steps = {tc.id: [{"step": s.step, "action": s.action, "target": s.target, "value": s.value, "description": s.description} for s in tc.steps] for tc in test_cases}
    if not present_tc_to_user(test_cases, auto_yes=auto_yes):
        typer.echo("Execution cancelled by user.")
        return None

    # ── Phase 3.5: Delta extraction and learning ─────────────────────────
    if memory_app_id:
        try:
            from testagent.plan.delta_extractor import process_deltas_and_confirm
            from testagent.rag.factories import create_pipeline as _create_pipe
            _rag_pipe = _create_pipe(settings)
            await process_deltas_and_confirm(
                test_cases=test_cases,
                original_steps=original_steps,
                app_id=memory_app_id,
                plan_name=name,
                llm_callable=_build_llm_callable(),
                rag_pipeline=_rag_pipe,
            )
        except Exception as exc:
            typer.echo(f"  [Delta extraction skipped: {exc}]")

    # ── Compute adoption score ───────────────────────────────────────────
    if memory_app_id:
        try:
            from testagent.memory.adoption_scorer import compute_adoption_score
            from testagent.rag.factories import create_pipeline as _create_pipe2

            _rag2 = _create_pipe2(settings)
            confirmed_text = serialize_cases_for_storage(test_cases)
            # Get retrieved items from the latest trace
            retrieved = []
            try:
                from testagent.db.engine import get_session
                from testagent.db.repository import RetrievalTraceRepository

                async with get_session() as session:
                    repo = RetrievalTraceRepository(session)
                    traces = await repo.get_by_app_id(memory_app_id, limit=1)
                    if traces and traces[0].retrieved_items:
                        retrieved = traces[0].retrieved_items
            except Exception:
                pass

            if retrieved:
                score = await compute_adoption_score(
                    [confirmed_text], retrieved, _rag2._embedding_service.embed,
                )
                # Backfill the latest trace
                try:
                    async with get_session() as session:
                        repo = RetrievalTraceRepository(session)
                        traces = await repo.get_by_app_id(memory_app_id, limit=1)
                        if traces:
                            await repo.update(traces[0].id, {"adoption_score": score})
                    typer.echo(f"  Adoption score: {score:.2f}")
                except Exception:
                    pass
        except Exception as exc:
            typer.echo(f"  [Adoption score skipped: {exc}]")

    # ── Persist confirmed cases to App Context Memory ──────────────────
    if memory_app_id:
        # ── Record app version if available ───────────────────────────
        try:
            from testagent.db.engine import get_session
            from testagent.db.repository import AppVersionRepository

            async with get_session() as session:
                av_repo = AppVersionRepository(session)
                existing = await av_repo.get_by_app_id(memory_app_id)
                if not existing:
                    await av_repo.upsert(memory_app_id, version="unknown", updated_by="plan_command")
        except Exception:
            pass

        # ── RAG write-back ────────────────────────────────────────────
        try:
            from testagent.rag.factories import create_pipeline

            rag_pipeline = create_pipeline(settings)
            cases_text = serialize_cases_for_storage(test_cases)
            if cases_text:
                await rag_pipeline.write_back(
                    content=cases_text,
                    collection="app_test_cases",
                    metadata={
                        "app_id": memory_app_id,
                        "plan_name": name,
                        "case_count": len(test_cases),
                    },
                )
                typer.echo(f"  Saved {len(test_cases)} case(s) to App Context Memory.")
        except Exception as exc:
            typer.echo(f"  [App Context Memory write-back skipped: {exc}]")

        # ── Dual-write: SQLite TestCaseRecord ────────────────────────
        try:
            from testagent.db.engine import get_session
            from testagent.db.repository import TestCaseRecordRepository, AppVersionRepository
            from testagent.models.test_case_record import TestCaseRecord

            # Resolve app version from AppVersion table, fallback to ""
            app_version = ""
            try:
                async with get_session() as session:
                    av_repo = AppVersionRepository(session)
                    av_record = await av_repo.get_by_app_id(memory_app_id)
                    if av_record:
                        app_version = av_record.current_version or ""
            except Exception:
                pass

            async with get_session() as session:
                tcr_repo = TestCaseRecordRepository(session)
                for tc in test_cases:
                    tc_content = serialize_cases_for_storage([tc])
                    await tcr_repo.create(TestCaseRecord(
                        app_id=memory_app_id,
                        app_version=app_version,
                        case_content=tc_content,
                        source="generated",
                        original_case_id=tc.id,
                        confidence=0.5,
                        tags=",".join(tc.requirement_ids) if tc.requirement_ids else "",
                        scope="app_local",
                    ))
                typer.echo(f"  Saved {len(test_cases)} case record(s) to SQLite.")
        except Exception as exc:
            typer.echo(f"  [TestCaseRecord write skipped: {exc}]")

    # ── Phase 4: Execute all TCs ────────────────────────────────────────────
    typer.echo("Executing test cases...")

    # Ensure Appium is still healthy before execution
    from testagent.common.appium_manager import ensure_appium_running

    if not await ensure_appium_running():
        typer.echo("❌ Appium server is not available. Please start Appium manually.")
        raise typer.Exit(1)

    engine = ExecutionEngine(config, llm_provider=llm_provider)
    executed_tcs = await engine.execute_all(test_cases)

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
