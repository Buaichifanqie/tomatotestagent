from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
import yaml
from concurrent.futures import ThreadPoolExecutor, as_completed
from rich.console import Console

from testagent.plan.execution_engine import ExecutionEngine
from testagent.plan.session_manager import SessionManager
from testagent.plan.evaluator import PerTCEvaluator
from testagent.plan.models import ExecutionStatus, ExecutionVerdict, OverallEvaluation, PlanConfig, TestCase, TestStep
from testagent.plan.overall_evaluator import OverallEvaluator
from testagent.plan.prd_parser import PrdParser
from testagent.plan.report_generator import ReportGenerator
from testagent.plan.test_case_generator import TestCaseGenerator
from testagent.rag.app_memory import (
    format_retrieved_cases_for_prompt,
    serialize_cases_for_storage,
)

from dataclasses import dataclass, field


@dataclass
class DeviceInfo:
    """Minimal device descriptor for multi-device orchestration."""
    udid: str
    name: str = ""
    appium_url: str = "http://localhost:4723"
    system_port: int = 8200


@dataclass
class DevicePlanAssignment:
    """Pairs a device with a test plan path."""
    device: DeviceInfo
    plan_path: str


@dataclass
class PlanResult:
    """Structured result from a single plan execution."""

    status: str  # "completed" | "failed"
    requirement_source: str  # original input (file path / URL / text)
    test_cases: list[TestCase] = field(default_factory=list)
    report_path: str = ""
    summary: str = ""  # human-readable text summary
    error: str | None = None
    case_count: int = 0
    passed: int = 0
    failed: int = 0
    aborted: int = 0
    duration: str = ""


# ── helper functions ─────────────────────────────────────────────────────────


async def _detect_app_id(
    requirement: str,
    device_udid: str = "",
    platform: str = "android",
) -> str | None:
    """Auto-detect app identifier from connected device using platform tools."""
    from testagent.platform.factory import PlatformFactory

    platform_obj = PlatformFactory.create(platform)

    # Check device connection
    try:
        devices = await platform_obj.list_connected_devices()
        if not devices:
            typer.echo(f"  [{platform}: no device connected, cannot auto-detect app]")
            return None
    except FileNotFoundError:
        typer.echo(f"  [{platform} tools not found, cannot auto-detect app]")
        return None
    except Exception as exc:
        typer.echo(f"  [{platform} check failed: {exc}]")
        return None

    # List installed 3rd-party apps
    try:
        apps = await platform_obj.detect_installed_apps(device_udid)
        if not apps:
            typer.echo(f"  [no {platform} 3rd-party apps found on device]")
            return None
    except Exception as exc:
        typer.echo(f"  [failed to list apps: {exc}]")
        return None

    # LLM matching
    from testagent.config.settings import get_settings
    from testagent.llm.local_provider import LLMProviderFactory

    settings = get_settings()
    provider = LLMProviderFactory.create(settings)

    app_list = "\n".join(f"  {i+1}. {p}" for i, p in enumerate(apps))
    prompt = (
        f"用户需求: {requirement}\n\n"
        f"设备上已安装的{platform}第三方应用列表:\n{app_list}\n\n"
        f"请根据用户需求，选择最匹配的{platform}应用标识符。"
        f"只输出标识符本身，不要任何额外文字。"
    )

    try:
        response = await provider.chat(
            system=f"你是一个{platform}工程师，擅长根据应用名称匹配包名/bundleId。",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
        for block in response.content:
            if block.get("type") == "text":
                matched = str(block.get("text", "")).strip()
                if matched in apps:
                    typer.echo(f"  [auto-detected app: {matched}]")
                    return matched
                typer.echo(f"  [LLM suggested '{matched}' but not in the installed list]")
                return None
    except Exception as exc:
        typer.echo(f"  [LLM matching failed: {exc}]")
    return None


async def _detect_app_version(
    app_id: str,
    device_udid: str = "",
    platform: str = "android",
) -> str | None:
    """Auto-detect app version from connected device using platform tools."""
    from testagent.platform.factory import PlatformFactory
    platform_obj = PlatformFactory.create(platform)
    return await platform_obj.detect_app_version(app_id, device_udid)


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


def format_tc_summary(test_cases: list[TestCase], show_steps: bool = False) -> str:
    """Format a human-readable summary of generated test cases.

    Args:
        test_cases: The list of ``TestCase`` objects to summarise.
        show_steps: When ``True``, expand all steps inline.

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
        regression_tag = " [回归]" if tc.is_regression else ""
        step_count = f" ({len(tc.steps)}步)" if tc.steps else ""
        lines.append(f"  {tc.id}: {tc.title} {priority_tag}{core_tag}{regression_tag}{step_count}")

        # ── Steps inline when expanded ────────────────────────────────
        if show_steps and tc.steps:
            for s in tc.steps:
                action_icon = {
                    "tap": "👆", "type": "⌨️", "swipe": "👉",
                    "assert": "✅", "launch": "🚀", "exec": "⚙️",
                    "screenshot": "📷", "wait": "⏳",
                }.get(s.action, "  ")
                step_desc = f"      {action_icon} Step {s.step}: [{s.action}]"
                if s.target:
                    step_desc += f" → {s.target}"
                if s.value:
                    step_desc += f"  (输入: {s.value[:40]}{'...' if len(s.value) > 40 else ''})"
                if s.tap_first:
                    step_desc += f"  [先点: {s.tap_first}]"
                lines.append(step_desc)
            lines.append("")

    lines.append(f"Total: {len(test_cases)} test case(s)")
    return "\n".join(lines)


def present_tc_to_user(test_cases: list[TestCase], auto_yes: bool, llm_provider: Any = None, app_package: str = "") -> bool:
    """Present test cases to the user for confirmation and optional editing.

    Args:
        test_cases: The list of generated ``TestCase`` objects (modified in-place).
        auto_yes: When ``True``, always return ``True`` without prompting.
        llm_provider: Optional LLM provider for auto-generating steps.
        app_package: App package name for step generation context.

    Returns:
        ``True`` when the user confirms (or ``auto_yes`` is set), ``False``
        when the user rejects or the list is empty.
    """
    if auto_yes:
        summary = format_tc_summary(test_cases, show_steps=True)
        if summary:
            typer.echo(summary)
        return True

    if not test_cases:
        typer.echo("No test cases generated.")
        return False

    while True:
        summary = format_tc_summary(test_cases, show_steps=False)
        typer.echo(summary)
        typer.echo("")
        typer.echo("  [y] 执行  [v1] 查看用例1步骤  [r1,3] 标记回归  [e] 编辑  [n] 取消")
        choice_raw = typer.prompt("  请选择", default="y", show_default=False)
        choice = choice_raw.lower().strip()

        if choice == "y":
            return True
        if choice == "n":
            return False

        # Combined commands: v1, v2, v3 (view details directly)
        if choice.startswith("v") and len(choice) > 1:
            try:
                idx = int(choice[1:]) - 1
                if 0 <= idx < len(test_cases):
                    _show_tc_steps(test_cases[idx])
                else:
                    typer.echo(f"  编号 {choice[1:]} 超出范围")
            except ValueError:
                typer.echo("  无效格式，请输入 v1 / v2 / v3 ...")
            continue

        # Combined commands: r1, r1,3,5 (toggle regression directly)
        if choice.startswith("r") and len(choice) > 1:
            import re
            nums = re.split(r"[,\s]+", choice[1:])
            for n in nums:
                if not n.strip():
                    continue
                try:
                    idx = int(n.strip()) - 1
                    if 0 <= idx < len(test_cases):
                        test_cases[idx].is_regression = not test_cases[idx].is_regression
                        status = "回归" if test_cases[idx].is_regression else "取消回归"
                        typer.echo(f"    {test_cases[idx].id}: {status}")
                    else:
                        typer.echo(f"    编号 {n} 超出范围")
                except ValueError:
                    typer.echo(f"    无效编号: {n}")
            continue

        if choice == "v":
            _tc_view_details(test_cases)
            continue
        if choice == "r":
            _tc_mark_regression(test_cases)
            continue
        if choice == "e":
            _tc_editor(test_cases, llm_provider=llm_provider, app_package=app_package)
            continue
        typer.echo("  无效输入，请输入 y / v / r / e / n")


def _tc_editor(test_cases: list[TestCase], llm_provider: Any = None, app_package: str = "") -> None:
    """Interactive sub-editor for add / delete / modify test cases."""
    while True:
        typer.echo("")
        typer.echo("  ── 编辑用例 ──")
        typer.echo("  [a] 添加用例  [d] 删除用例  [m] 修改用例  [b] 返回")
        action = typer.prompt("  请选择", default="b", show_default=False)

        if action.lower() == "b":
            return
        if action.lower() == "a":
            _tc_add(test_cases, llm_provider=llm_provider, app_package=app_package)
        elif action.lower() == "d":
            _tc_delete(test_cases)
        elif action.lower() == "m":
            _tc_modify(test_cases)
        else:
            typer.echo("  无效输入，请输入 a / d / m / b")


def _tc_add(test_cases: list[TestCase], llm_provider: Any = None, app_package: str = "") -> None:
    """Add test cases interactively with AI-generated steps."""
    import asyncio

    typer.echo("")
    typer.echo("  ── 添加新用例（添加完一个后可继续添加）──")
    while True:
        tc_id = typer.prompt("  用例 ID（如 TC-NEW-001）")
        title = typer.prompt("  用例标题")
        priority = typer.prompt("  优先级", default="P1")
        is_core = typer.confirm("  是否为核心用例", default=False)
        requirement_ids_str = typer.prompt("  关联需求 ID（逗号分隔，留空跳过）", default="")
        requirement_ids = [r.strip() for r in requirement_ids_str.split(",") if r.strip()] if requirement_ids_str else []

        # ── AI-generate steps from title ──────────────────────────────
        steps: list[TestStep] = []
        if llm_provider:
            typer.echo("  正在用 AI 生成测试步骤...")
            try:
                steps = asyncio.run(_generate_steps_for_title(llm_provider, title, app_package))
            except Exception as exc:
                typer.echo(f"  [AI 生成失败: {exc}，将手动输入步骤]")

        if steps:
            typer.echo("  AI 生成的步骤:")
            for s in steps:
                typer.echo(f"    {s.step}. [{s.action}] target={s.target}  value={s.value}")
            use_ai = typer.confirm("  使用这些步骤?", default=True)
            if not use_ai:
                steps = []

        # ── Manual step input fallback ────────────────────────────────
        if not steps:
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


async def _generate_steps_for_title(
    llm_provider: Any,
    title: str,
    app_package: str = "",
) -> list[TestStep]:
    """Use LLM to generate test steps for a given test case title."""
    pkg_info = f"\nApp 包名: {app_package}" if app_package else ""
    prompt = f"""根据以下测试用例标题，生成具体的 Android App 测试步骤。

用例标题: {title}{pkg_info}

要求:
- 每个步骤必须有 action、target、value（可为空字符串）
- action 只能是: tap, type, swipe, assert, launch, exec, screenshot, wait
- 第一步通常是 launch（启动 App）
- target 用中文描述 UI 元素，如"搜索框"、"确认按钮"
- assert 步骤的 target 描述预期结果
- 步骤数量 3-8 个

请直接输出 JSON 数组，不要其他文字。格式:
[{{"step": 1, "action": "launch", "target": "", "value": ""}}, ...]"""

    response = await llm_provider.chat(
        system="你是一个 Android 测试工程师，擅长编写 App 自动化测试步骤。只输出 JSON，不要其他文字。",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )

    import json
    import re

    text = ""
    for block in response.content:
        if block.get("type") == "text":
            text = block.get("text", "")
            break

    # Extract JSON array from response
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []

    raw_steps = json.loads(match.group())
    steps: list[TestStep] = []
    for i, s in enumerate(raw_steps, 1):
        steps.append(TestStep(
            step=s.get("step", i),
            action=s.get("action", "tap"),
            target=s.get("target", ""),
            value=s.get("value", ""),
        ))
    return steps


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
    typer.echo(f"\n  ── 编辑步骤: {tc.id} ──")
    _display_steps(tc)

    while True:
        typer.echo("")
        typer.echo("  [a] 添加步骤  [d] 删除步骤  [m] 修改步骤  [b] 返回")
        action = typer.prompt("  请选择", default="b", show_default=False)

        if action.lower() == "b":
            return
        elif action.lower() == "a":
            step_num = len(tc.steps) + 1
            typer.echo(f"\n  添加第 {step_num} 步:")
            a = typer.prompt("    action", default="tap")
            target = typer.prompt('    target (如 搜索框/登录按钮)', default="")
            value = typer.prompt("    value（输入内容，留空跳过）", default="")
            expected = typer.prompt("    expected（预期结果，留空跳过）", default="")
            tap_first = typer.prompt("    tap_first（先点击的区域，留空跳过）", default="")
            tc.steps.append(TestStep(
                step=step_num, action=a, target=target,
                value=value, expected=expected, tap_first=tap_first,
            ))
            _display_steps(tc)
        elif action.lower() == "d":
            if not tc.steps:
                typer.echo("  没有可删除的步骤")
                continue
            idx_str = typer.prompt("  输入要删除的步骤编号")
            try:
                idx = int(idx_str) - 1
            except ValueError:
                typer.echo("  无效编号")
                continue
            if 0 <= idx < len(tc.steps):
                removed = tc.steps.pop(idx)
                for i, s in enumerate(tc.steps):
                    s.step = i + 1
                typer.echo(f"  ✅ 已删除步骤 {removed.step}: [{removed.action}] {removed.target}")
                _display_steps(tc)
            else:
                typer.echo("  编号超出范围")
        elif action.lower() == "m":
            if not tc.steps:
                typer.echo("  没有可修改的步骤")
                continue
            idx_str = typer.prompt("  输入要修改的步骤编号")
            try:
                idx = int(idx_str) - 1
            except ValueError:
                typer.echo("  无效编号")
                continue
            if not (0 <= idx < len(tc.steps)):
                typer.echo("  编号超出范围")
                continue
            s = tc.steps[idx]
            typer.echo(f"\n  修改步骤 {s.step}:")
            s.action = typer.prompt("    action", default=s.action)
            s.target = typer.prompt("    target", default=s.target)
            s.value = typer.prompt("    value", default=s.value)
            s.expected = typer.prompt("    expected", default=s.expected)
            s.tap_first = typer.prompt("    tap_first", default=s.tap_first)
            typer.echo("  ✅ 已修改")
            _display_steps(tc)


def _display_steps(tc: TestCase) -> None:
    """Display steps of a test case in a clear table format."""
    if not tc.steps:
        typer.echo("    （无步骤）")
        return
    typer.echo("")
    for s in tc.steps:
        action_icon = {
            "tap": "👆", "type": "⌨️", "swipe": "👉",
            "assert": "✅", "launch": "🚀", "exec": "⚙️",
            "screenshot": "📷", "wait": "⏳",
        }.get(s.action, "  ")
        desc = f"    {action_icon} #{s.step} [{s.action}]"
        if s.target:
            desc += f" → {s.target}"
        if s.value:
            desc += f"  value=\"{s.value[:50]}{'...' if len(s.value) > 50 else ''}\""
        if s.expected:
            desc += f"  expect=\"{s.expected[:40]}{'...' if len(s.expected) > 40 else ''}\""
        if s.tap_first:
            desc += f"  [先点: {s.tap_first}]"
        typer.echo(desc)


# ── 查看用例详情（折叠展开）───────────────────────────────────────────────


def _show_tc_steps(tc: TestCase) -> None:
    """Print steps for a single test case (used by v1, v2, etc.)."""
    typer.echo(f"\n  ── {tc.id}: {tc.title} ({len(tc.steps)}步) ──")
    if not tc.steps:
        typer.echo("    （无步骤）")
        return
    for s in tc.steps:
        action_icon = {
            "tap": "👆", "type": "⌨️", "swipe": "👉",
            "assert": "✅", "launch": "🚀", "exec": "⚙️",
            "screenshot": "📷", "wait": "⏳",
        }.get(s.action, "  ")
        desc = f"    {action_icon} #{s.step} [{s.action}]"
        if s.target:
            desc += f" → {s.target}"
        if s.value:
            desc += f"  value=\"{s.value[:50]}\""
        if s.expected:
            desc += f"  expect=\"{s.expected[:40]}\""
        if s.tap_first:
            desc += f"  [先点: {s.tap_first}]"
        typer.echo(desc)


def _tc_view_details(test_cases: list[TestCase]) -> None:
    """Show steps for a specific test case by number (accordion-style)."""
    if not test_cases:
        typer.echo("  没有可查看的用例")
        return

    typer.echo("")
    typer.echo("  ── 查看用例详情 ──")
    typer.echo("  输入编号查看该用例的详细步骤（如: 1）")
    for i, tc in enumerate(test_cases):
        reg_mark = " [回归]" if tc.is_regression else ""
        typer.echo(f"    {i+1}. {tc.id}: {tc.title} ({len(tc.steps)}步){reg_mark}")

    while True:
        choice = typer.prompt("  输入编号查看（b 返回）", default="b", show_default=False)
        if choice.lower() == "b":
            return
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(test_cases):
                _show_tc_steps(test_cases[idx])
            else:
                typer.echo("  编号超出范围")
        except ValueError:
            typer.echo("  无效输入")


# ── 标记回归用例 ──────────────────────────────────────────────────────────


def _tc_mark_regression(test_cases: list[TestCase]) -> None:
    """Toggle regression flag on individual test cases.

    Shows current regression status and lets user toggle by number.
    Only P0/P1 core flow cases should typically be marked as regression.
    """
    if not test_cases:
        typer.echo("  没有可标记的用例")
        return

    typer.echo("")
    typer.echo("  ── 标记回归用例 ──")
    typer.echo("  按编号切换回归标记，被标记的用例首次执行后会自动生成脚本")
    typer.echo("  后续回归执行时直接走脚本快速通道（不需要 LLM/Vision）")
    typer.echo("  建议只标记 P0/P1 核心流程用例为回归")
    typer.echo("")

    while True:
        for i, tc in enumerate(test_cases):
            reg_mark = " [回归]" if tc.is_regression else ""
            typer.echo(f"    {i+1}. [{tc.priority}] {tc.id}: {tc.title}{reg_mark}")
        typer.echo("")
        typer.echo("  输入编号切换标记（如: 1,3,5）输入 b 返回")
        choice = typer.prompt("  请选择", default="b", show_default=False)

        if choice.lower() == "b":
            # Show summary of changes
            reg_count = sum(1 for tc in test_cases if tc.is_regression)
            typer.echo(f"  ✅ 已标记 {reg_count} 个回归用例")
            return

        # Parse: "1,3,5" or "1 3 5"
        import re
        tokens = re.split(r"[,\s]+", choice.strip())
        for t in tokens:
            try:
                idx = int(t) - 1
                if 0 <= idx < len(test_cases):
                    test_cases[idx].is_regression = not test_cases[idx].is_regression
                    status = "回归" if test_cases[idx].is_regression else "取消回归"
                    typer.echo(f"    {test_cases[idx].id}: {status}")
                else:
                    typer.echo(f"    编号 {t} 超出范围")
            except ValueError:
                typer.echo(f"    无效编号: {t}")


# ── 回归脚本生成 ──────────────────────────────────────────────────────────


def _generate_regression_scripts(
    test_cases: list[TestCase],
    config: PlanConfig,
    output_dir: str,
    app_name: str = "",
) -> None:
    """Generate regression scripts for successfully executed regression TCs.

    Only generates scripts for TCs that:
    - Have ``is_regression == True``
    - Were executed successfully (status == EXECUTED)
    """
    regression_tcs = [tc for tc in test_cases if tc.is_regression]
    if not regression_tcs:
        return

    from testagent.regression.script_generator import ScriptGenerator
    from testagent.regression.script_store import ScriptStore

    generator = ScriptGenerator(output_dir=output_dir)
    store = ScriptStore()  # 使用集中化存储（./scripts/）

    generated = 0
    for tc in regression_tcs:
        if tc.execution.status != ExecutionStatus.EXECUTED:
            typer.echo(f"  [Script skipped: {tc.id} — execution failed]")
            continue

        # Check if script already exists by TC ID
        existing = store.load(tc.id)
        if existing and existing.status.value != "deprecated":
            typer.echo(f"  [Script exists: {tc.id} — skipping generation]")
            tc.script_path = str(store._root / f"{tc.id}.json")
            continue

        # Cross-run match: find similar script by title across ALL reports
        if not existing:
            matched = ScriptStore.find_across_reports(
                title=tc.title,
                app_name=app_name or config.app_package or "",
                min_similarity=0.4,
            )
            if matched:
                matched.tc_id = tc.id
                matched.tc_title = tc.title
                ver = getattr(config, "app_version", "")
                if ver and ver not in matched.compatible_versions:
                    matched.compatible_versions.append(ver)
                script_path = store.save(matched)
                tc.script_path = str(store._root / f"{tc.id}.json")
                typer.echo(f"  [Script matched: '{tc.title}' <- script from {matched.tc_id}]")
                continue

        # Generate new script
        try:
            # Use default screen size (1080x2400), coords are normalized
            script = generator.generate(
                tc=tc,
                app_name=app_name,
                app_package=config.app_package or "",
                app_version=getattr(config, "app_version", ""),
                platform=config.platform,
                screen_width=1080,
                screen_height=2400,
            )
            script_path = store.save(script)
            tc.script_path = str(store._root / f"{tc.id}.json")
            typer.echo(f"  [Script generated: {tc.id} -> {script_path.name}]")
            generated += 1
        except Exception as e:
            typer.echo(f"  [Script generation failed for {tc.id}: {e}]")

    if generated:
        typer.echo(f"  Generated {generated} regression script(s)")


# ── main orchestration ───────────────────────────────────────────────────────


def plan_command(
    requirement: str,
    name: str = "",
    platform: str = "android",
    app_package: str = "",
    app_activity: str = "",
    app_id: str = "",
    auto_yes: bool = False,
    device_udid: str = "",
    appium_url: str = "http://localhost:4723",
    system_port: int = 8200,
) -> tuple[str | None, OverallEvaluation | None, list[TestCase]]:
    """Main orchestration function — sync entry point for the Typer CLI.

    Wraps the async implementation in ``asyncio.run()``. See
    ``_plan_command_async`` for the full docstring.
    """
    return asyncio.run(_plan_command_async(
        requirement, name=name,
        platform=platform,
        app_package=app_package, app_activity=app_activity,
        app_id=app_id, auto_yes=auto_yes,
        device_udid=device_udid, appium_url=appium_url, system_port=system_port,
    ))


async def _plan_command_async(
    requirement: str,
    name: str = "",
    platform: str = "android",
    app_package: str = "",
    app_activity: str = "",
    app_id: str = "",
    auto_yes: bool = False,
    resume_dir: str = "",
    # New device parameters
    device_udid: str = "",
    appium_url: str = "http://localhost:4723",
    system_port: int = 8200,
    element_source: str = "multimodal",
) -> tuple[str | None, OverallEvaluation | None, list[TestCase]]:
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
        platform: Target platform ("android" or "ios").
        app_package: App identifier (Android→packageName, iOS→bundleId).
        app_activity: [Android only] App launch activity.
        auto_yes: Skip the user confirmation step.

    Returns:
        A tuple of (report_path, overall_evaluation, executed_test_cases).
        report_path is ``None`` if the pipeline was aborted.
    """
    # ── Resume mode ───────────────────────────────────────────────────────
    if resume_dir:
        return await _resume_plan(resume_dir, llm_provider=None, log_fn=typer.echo)

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

    # ── Auto-detect app id if not provided ──────────────────────────
    if not app_package:
        detected = await _detect_app_id(requirement, device_udid=device_udid, platform=platform)
        if detected:
            app_package = detected

    # ── Derive app identifier for App Context Memory ────────────────────
    memory_app_id = app_id or app_package  # explicit --app-id takes priority

    # ── Auto-detect app version from device ─────────────────────────────
    detected_version: str | None = None
    if app_package:
        detected_version = await _detect_app_version(app_package, device_udid=device_udid, platform=platform)
        if detected_version:
            typer.echo(f"  [auto-detected app version: {detected_version}]")

    # ── Load App Skill ───────────────────────────────────────────────────
    skill_app_name: str | None = None
    skill_ui_knowledge: str = ""   # UI 知识层：视觉特征 + 元素名称（注入生成阶段）
    skill_full_content: str = ""   # 完整内容：含执行策略（注入执行阶段）
    _skill_loader = None
    if app_package:
        from testagent.skills.app_skill_loader import AppSkillLoader
        from pathlib import Path as _Path
        _skill_loader = AppSkillLoader(apps_dir=_Path("skills") / "apps")
        skill_app_name = _skill_loader.find_app_by_package(app_package)
        if skill_app_name:
            sub_skills = [f.file_path.stem for f in _skill_loader.load_app(skill_app_name) if not f.is_main]
            sub_str = ", ".join(sub_skills) if sub_skills else ""
            skill_label = f"{skill_app_name} ({sub_str})" if sub_str else skill_app_name
            typer.echo(f"  Found skill: {skill_label}")
            if typer.confirm(f"  Load skill '{skill_app_name}'?", default=True):
                # UI 知识层：只含视觉特征和元素名称，供 TC 生成阶段使用
                skill_ui_knowledge = _skill_loader.get_ui_knowledge(skill_app_name, requirement)
                # 完整内容：含执行策略（tap_first、弹窗处理等），供执行引擎使用
                skill_full_content = _skill_loader.get_matching_content(skill_app_name, requirement)
                if not skill_full_content:
                    skill_full_content = _skill_loader.get_summary(skill_app_name) or ""
                if skill_ui_knowledge:
                    typer.echo(f"  [Loaded skill: {skill_label} (UI knowledge + execution strategy)]")
                else:
                    typer.echo(f"  [Loaded skill: {skill_label} (execution strategy only)]")
            else:
                skill_app_name = None
                typer.echo("  [Skill skipped]")

    # ── Set up output directory ─────────────────────────────────────────────
    output_dir = setup_output_dir(name)
    typer.echo(f"Output directory: {output_dir}")

    # ── Set up file logging ────────────────────────────────────────────────
    from testagent.common.logging import setup_file_logging
    log_path = setup_file_logging(output_dir)
    typer.echo(f"  Log file: {log_path}")

    config = PlanConfig(
        name=name,
        platform=platform,
        app_package=app_package,
        app_activity=app_activity,
        output_dir=output_dir,
        auto_yes=auto_yes,
        device_udid=device_udid,
        appium_url=appium_url,
        system_port=system_port,
        element_source=element_source,
    )

    # Create LLM provider once — shared between exploration, TC generation and execution
    from testagent.config.settings import get_settings
    from testagent.llm.local_provider import LLMProviderFactory
    from testagent.common.token_tracker import TokenTracker
    from testagent.common.tracking_provider import TrackingLLMProvider

    settings = get_settings()
    _raw_llm_provider = LLMProviderFactory.create(settings)
    token_tracker = TokenTracker()
    llm_provider = TrackingLLMProvider(_raw_llm_provider, token_tracker, category="llm")

    def _build_llm_callable() -> Any:
        """Build a callable (async) that wraps the shared LLM provider."""
        from testagent.plan.test_case_generator import TC_GENERATION_SYSTEM_PROMPT

        async def _call(text: str) -> str:
            response = await llm_provider.chat(
                system=TC_GENERATION_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": text}],
                max_tokens=32768,
                temperature=0.1,
            )
            for block in response.content:
                if block.get("type") == "text":
                    return str(block.get("text", ""))
            return ""

        return _call  # return async callable directly (no asyncio.run wrapper)

    def _build_critic_callable() -> Any:
        """Build a callable for Critic (uses Judge model, different from Actor)."""
        from testagent.llm.openai_provider import OpenAIProvider
        import types
        critic = object.__new__(OpenAIProvider)
        critic._api_key = settings.critic_api_key.get_secret_value() if settings.critic_api_key else settings.openai_api_key.get_secret_value()
        critic._model = settings.critic_model or settings.openai_model
        critic._base_url = settings.critic_api_url or settings.openai_base_url
        critic._client = None
        critic._rate_limiter = types.SimpleNamespace(acquire=lambda p: __import__('asyncio').sleep(0))
        critic._budget_manager = types.SimpleNamespace(check_budget=lambda t, m: True, record=lambda t, m, r: None)

        async def _call(text: str) -> str:
            response = await critic_provider.chat(
                system="你是一个严谨的测试架构师，专注于发现用例覆盖的盲区。",
                messages=[{"role": "user", "content": text}],
                max_tokens=8192,
                temperature=0.1,
            )
            for block in response.content:
                if block.get("type") == "text":
                    return str(block.get("text", ""))
            return ""

        return _call

    # ── Phase 1.5: App UI Exploration (disabled — 0 elements, wastes time) ──
    ui_context_map = None
    ui_context_string = ""
    if False and app_package:  # disabled
        typer.echo("[Phase 1.5] Exploring App UI...")
        try:
            from testagent.exploration.app_explorer import AppExplorer
            from testagent.exploration.map_cache import MapCache
            from testagent.exploration.ui_context_map import UIContextMap

            cache = MapCache(cache_dir=output_dir)

            # Try to use cached map
            cached_map = cache.load(app_package, detected_version or "unknown")
            if cached_map:
                typer.echo("  Found cached UI context map, validating...")
                # Quick validation: create session, get home elements, compare
                sm_temp = SessionManager(appium_url=config.appium_url)
                sid = sm_temp.create_session()
                if sid:
                    try:
                        from testagent.mcp_servers.appium_server.tools import app_launch, app_get_source
                        await app_launch(package=app_package, activity=app_activity or "",
                                         appium_url=sm_temp.appium_url, session_id=sid)
                        await asyncio.sleep(3)
                        src = await app_get_source(appium_url=sm_temp.appium_url, session_id=sid)
                        from testagent.exploration.ui_tree_parser import parse_ui_tree
                        from testagent.exploration.ui_context_map import ElementInfo as EI
                        home_elements = parse_ui_tree(src.get("source", ""))
                        current_eis = [EI.from_ui_element(e) for e in home_elements]
                        if cache.validate(app_package, detected_version or "unknown", current_eis):
                            ui_context_map = cached_map
                            typer.echo("  Cache validated, using cached UI context map")
                        else:
                            typer.echo("  Cache validation failed, will re-explore")
                    finally:
                        sm_temp.close_session()
                else:
                    typer.echo("  Cannot create session for cache validation, will re-explore")
                    sm_temp.close_session()

            # Explore if no valid cache
            if ui_context_map is None:
                sm = SessionManager(appium_url=config.appium_url)
                explorer = AppExplorer(
                    session_manager=sm,
                    llm_callable=_build_llm_callable(),
                    appium_url=sm.appium_url,
                )
                ui_context_map = await explorer.explore(
                    prd_text=prd_text,
                    app_package=app_package,
                    app_activity=app_activity or "",
                )

                if ui_context_map.pages:
                    cache.save(app_package, detected_version or "unknown", ui_context_map)
                    typer.echo(f"  Explored {len(ui_context_map.pages)} pages, {ui_context_map.element_count} elements")
                else:
                    typer.echo("  No pages explored, continuing without UI context")

            if ui_context_map and ui_context_map.pages:
                ui_context_string = ui_context_map.to_context_string()

                # Save to output_dir for debugging
                map_path = Path(output_dir) / "ui_context_map.json"
                map_path.write_text(
                    json.dumps(ui_context_map.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

        except Exception as e:
            typer.echo(f"  [WARNING] App exploration failed: {e}")
            typer.echo("  Continuing without UI context (TC quality may be lower)")
            ui_context_map = None
            ui_context_string = ""

    # ── Phase 2: Generate test cases ────────────────────────────────────────
    typer.echo("Generating test cases...")

    # ── Inject scope constraint for short/raw text inputs ─────────────
    enhanced_prd = prd_text
    if not is_file and len(prd_text) < 200:
        enhanced_prd = (
            f"# 测试范围限定\n\n"
            f"**约束**：只为以下需求描述所属的功能域生成测试用例，"
            f"不要为无关功能生成用例。\n"
            f"**重要**：需求描述较短，你必须基于通用测试常识，"
            f"完整覆盖该功能域的核心交互路径，包括正向操作、逆向操作和异常场景。\n\n"
            f"## 用户需求\n\n{prd_text}"
        )

    # ── Inject app info into TC generation prompt ──────────────────────
    app_info_parts = []
    if app_package:
        if platform == "ios":
            app_info_parts.append(f"Platform: iOS")
            app_info_parts.append(f"iOS bundleId: {app_package}")
            app_info_parts.append(f"App launch: mobile: launchApp with bundleId")
            app_info_parts.append(f"Element strategies: accessibility_id, ios_predicate, ios_class_chain, xpath")
            app_info_parts.append(f"Back navigation: W3C /session/back (no KEYCODE_BACK)")
        else:
            app_info_parts.append(f"Platform: Android")
            app_info_parts.append(f"Android package name: {app_package}")
            if app_activity:
                app_info_parts.append(f"Android launch activity: {app_activity}")
            app_info_parts.append(f"Element strategies: accessibility_id, uiautomator, xpath")
    if app_info_parts:
        enhanced_prd += "\n\n" + "\n".join(app_info_parts)

    # ── Inject login hint if app has account config ───────────────────
    if app_package:
        from testagent.plan.app_accounts import has_login_config
        if has_login_config(app_package):
            enhanced_prd += (
                "\n\n## 登录信息\n\n"
                "此 App 已配置登录账号。登录由框架自动处理，**不要在测试步骤中编写登录/登出操作**。\n"
                "需要登录态的用例，设置 `required_state` 包含 `\"logged_in\"`。\n"
                "需要登录态的用例，第一步应从 App 首页开始（假设已完成登录）。\n"
                "不需要登录态的用例，`required_state` 设为空数组 `[]`。"
            )

    if ui_context_string:
        enhanced_prd += "\n\n## App 界面信息\n\n以下是通过自动化探索获取的 App 实际界面信息，包括各页面的可交互元素和导航路径。请基于这些真实信息生成测试用例步骤，不要猜测元素名称。\n\n" + ui_context_string

    # ── Inject App UI knowledge (visual features + element names only) ──
    # 注入 UI 知识层：告诉 LLM "有哪些 UI 元素"，但不告诉它 "怎么操作"。
    # 执行策略（tap_first、弹窗处理等）由 ExecutionEngine 在执行阶段自行加载。
    if skill_ui_knowledge:
        enhanced_prd += (
            f"\n\n## App UI 知识（来自 {skill_app_name} skill）\n\n"
            f"以下是该 App 的视觉特征和可用 UI 元素名称。"
            f"请在生成测试步骤时使用这些**准确的元素名称**，不要自行编造。\n\n"
            f"{skill_ui_knowledge}"
        )

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

            # ── Filter by functional relevance ────────────────────────────
            from testagent.rag.app_memory import filter_by_functional_relevance
            before_count = len(cases)
            cases = filter_by_functional_relevance(cases, user_intent=prd_text, app_package=app_package)
            filtered_count = before_count - len(cases)
            if filtered_count > 0:
                typer.echo(f"  [Filtered out {filtered_count} functionally irrelevant case(s)]")

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

    # Inject history context after the user's requirement (避免锚定偏见)
    if history_context:
        enhanced_prd = (
            f"{enhanced_prd}\n\n"
            "---\n"
            "## 附录：历史测试用例参考\n\n"
            "以下为系统检索到的历史用例。它们仅用于参考步骤格式和断言风格，\n"
            "**请完全忽略它们的功能范围，严格基于上方 PRD 覆盖所有功能模块**。\n"
            "如果新生成的用例与下列用例完全重复，请跳过；否则无需参考其内容。\n\n"
            f"{history_context}"
        )

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

    # ── Phase 2a: Inject standard cases (P0/P1 library) ────────────────
    from testagent.plan.standard_case_lib import StandardCaseLib

    std_lib = StandardCaseLib()
    std_prompt = std_lib.format_as_prompt(app_name=skill_app_name or "")
    if std_prompt:
        enhanced_prd = f"{enhanced_prd}\n\n{std_prompt}"
        std_count = len(std_lib.load_all(app_name=skill_app_name or ""))
        typer.echo(f"  [Standard cases: {std_count} P0/P1 cases loaded]")

    ts_gen = TestCaseGenerator(llm_provider=_build_llm_callable())

    _console = Console()

    token_tracker.start_generation()
    with _console.status("[bold green]Generating test cases, please wait...", spinner="dots"):
        test_cases = await ts_gen.generate(enhanced_prd, plan_name=name)
    token_tracker.end_generation()

    # ── Phase 2b: Actor-Critic cross-validation ─────────────────────────
    if test_cases:
        from testagent.plan.cross_validate import cross_validate, generate_supplementary

        try:
            missing = await cross_validate(requirement, test_cases, _build_critic_callable())
            if missing:
                typer.echo(f"  [Cross-validate: {len(missing)} missing scenario(s)]")
                for m in missing:
                    typer.echo(f"    - {m[:80]}")
                sup = await generate_supplementary(
                    requirement, test_cases, missing, _build_critic_callable(),
                )
                if sup:
                    test_cases.extend(sup)
                    typer.echo(f"  [+{len(sup)} supplementary case(s) added]")
        except Exception as exc:
            typer.echo(f"  [Cross-validate skipped: {exc}]")

    if not test_cases:
        typer.echo("No test cases generated. Aborting.")
        raw = ts_gen.last_raw_output
        if raw:
            typer.echo("\n--- Raw LLM output (first 2000 chars) ---")
            typer.echo(raw[:2000])
        return None, None, []

    typer.echo(f"Generated {len(test_cases)} test case(s).")

    # ── Fix LLM-hallucinated package names in generated TCs ─────────────────
    # The LLM sometimes hallucinates package names (e.g. typos) or copies
    # the template variable ${app_package} literally. Fix all of these.
    if app_package:
        for tc in test_cases:
            for step in tc.steps:
                if step.action == "launch":
                    step.target = app_package
                else:
                    # Replace template variable ${app_package} with actual package
                    step.target = step.target.replace("${app_package}", app_package)
                    step.value = step.value.replace("${app_package}", app_package)
                    # Replace hallucinated package names
                    for wrong in _HALLUCINATED_PACKAGES:
                        if wrong != app_package:
                            step.target = step.target.replace(wrong, app_package)
                            step.value = step.value.replace(wrong, app_package)

    # ── Auto-inject tap_first for hidden controls ──────────────────────────
    # LLM often generates plain `tap` for hidden controls instead of `tap_first`.
    # Post-process: if a step targets a hidden control (from skill's hidden_controls
    # config) and doesn't have tap_first, inject it automatically.
    if skill_app_name and _skill_loader is not None:
        hidden = _skill_loader.get_hidden_controls(skill_app_name)
        if hidden and hidden.get("targets") and hidden.get("trigger_area"):
            trigger_area = hidden["trigger_area"]
            hidden_targets = set(hidden["targets"])
            for tc in test_cases:
                for step in tc.steps:
                    if (step.action == "tap"
                            and step.target in hidden_targets
                            and not step.tap_first):
                        step.tap_first = trigger_area
                        typer.echo(f"  [Auto tap_first] {tc.id} step {step.step}: "
                                   f"'{step.target}' → tap_first='{trigger_area}'")

    # ── Phase 3: Present to user ────────────────────────────────────────────
    from testagent.plan.scheduler import reorder_for_execution
    test_cases = reorder_for_execution(test_cases)

    original_steps = {tc.id: [{"step": s.step, "action": s.action, "target": s.target, "value": s.value} for s in tc.steps] for tc in test_cases}
    if not present_tc_to_user(test_cases, auto_yes=auto_yes, llm_provider=llm_provider, app_package=app_package or ""):
        typer.echo("Execution cancelled by user.")
        return None, None, []

    # Save P0/P1 cases to standard library
    if skill_app_name:
        try:
            from testagent.plan.standard_case_lib import StandardCaseLib
            std_lib = StandardCaseLib()
            saved = 0
            for tc in test_cases:
                if tc.priority in ("P0", "P1"):
                    std_lib.save(tc, app_name=skill_app_name)
                    saved += 1
            if saved:
                typer.echo(f"  [Standard library: {saved} P0/P1 cases saved]")
        except Exception as exc:
            typer.echo(f"  [Standard library save skipped: {exc}]")

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
                    version = detected_version or "unknown"
                    await av_repo.upsert(memory_app_id, version=version, updated_by="plan_command")
                elif detected_version and existing.current_version != detected_version:
                    await av_repo.upsert(memory_app_id, version=detected_version, updated_by="auto_detect")
                    typer.echo(f"  [app version updated: {existing.current_version} -> {detected_version}]")
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

    actual_url = await ensure_appium_running(
        udid=config.device_udid, appium_url=config.appium_url,
    )
    if not actual_url:
        typer.echo("❌ Appium server is not available. Please start Appium manually.")
        raise typer.Exit(1)
    # Update config with the actual Appium URL (may differ if port was auto-assigned)
    if actual_url != config.appium_url:
        typer.echo(f"  [Appium: {actual_url}]")
        config.appium_url = actual_url

    # ── Infer states for execution (order preserved as generated) ───────
    from testagent.plan.scheduler import reorder_for_execution
    test_cases = reorder_for_execution(test_cases)
    typer.echo(f"  Execution order: {len(test_cases)} cases (original order)")

    # ── Set up checkpoint for crash/pause recovery ──────────────────────
    from testagent.plan.checkpoint import CheckpointManager

    ckpt = CheckpointManager(output_dir)
    ckpt.save(name, config, test_cases)

    # ── Extract toggle groups and hard rules from loaded skill ─────────
    skill_toggle_groups: list[list[str]] = []
    skill_hard_rules: str = ""
    if skill_app_name and _skill_loader is not None:
        skill_toggle_groups = _skill_loader.get_toggle_groups(skill_app_name)
        skill_hard_rules = _skill_loader.get_hard_rules(skill_app_name, requirement)

    # ── Init per-TC evaluator and judge ───────────────────────────────
    evaluator = PerTCEvaluator()
    judge = None
    try:
        from testagent.judge import CaseJudgeAgent, should_invoke_judge
        judge = CaseJudgeAgent(output_dir=output_dir, token_tracker=token_tracker)
    except Exception as exc:
        typer.echo(f"  [CaseJudgeAgent init failed: {exc}]")

    async def _on_tc_judged(tc):
        """Evaluate and judge a TC immediately after execution."""
        # 1. Save checkpoint
        try:
            ckpt.save(name, config, test_cases)
        except Exception as exc:
            typer.echo(f"  [Checkpoint save error: {exc}]")

        # 2. Skip evaluation for aborted TCs
        if tc.execution.status == ExecutionStatus.ABORTED:
            return

        # 3-6. Full evaluation (wrapped in try/except to prevent crash from empty errors)
        try:
            # 3. PerTCEvaluator step-level evaluation
            evaluation = evaluator.evaluate(tc)
            tc.execution.verdict = evaluation.verdict
            tc.execution.confidence = evaluation.confidence
            tc.execution.reason = evaluation.reason

            # 4. CaseJudgeAgent semantic evaluation (immediately, per-TC)
            judge_ran = False
            if judge is not None:
                needs_judge, level = should_invoke_judge(tc)
                if needs_judge:
                    try:
                        judge_result = await judge.evaluate(tc, level)
                        tc.execution.verdict = judge_result.verdict
                        tc.execution.confidence = judge_result.confidence
                        tc.execution.reason = judge_result.reasoning or judge_result.failure_root_cause
                        tc.execution.failure_category = judge_result.failure_category
                        tc.execution.failure_root_cause = judge_result.failure_root_cause
                        tc.execution.judge_evidence = judge_result.evidence
                        tc.execution.judge_confidence = judge_result.confidence
                        tc.execution.judge_reasoning = judge_result.reasoning
                        judge_ran = True
                    except Exception as exc:
                        typer.echo(f"  [Judge error for {tc.id}: {exc}]")

            # 5. Print final verdict with colors
            verdict = tc.execution.verdict
            status = tc.execution.status
            if judge_ran:
                _c = f" (confidence={tc.execution.judge_confidence:.2f})" if tc.execution.judge_confidence else ""
                _cat = f", category={tc.execution.failure_category}" if tc.execution.failure_category and tc.execution.failure_category != "NONE" else ""
                if verdict == ExecutionVerdict.PASS:
                    typer.echo(f"  \033[32m🤖 {tc.id} → PASS{_c}{_cat}\033[0m")
                elif verdict == ExecutionVerdict.FAIL:
                    typer.echo(f"  \033[31m🤖 {tc.id} → FAIL{_c}{_cat}\033[0m")
                elif verdict == ExecutionVerdict.NEED_REVIEW:
                    typer.echo(f"  \033[33m🤖 {tc.id} → NEED_REVIEW{_c}{_cat}\033[0m")
                else:
                    typer.echo(f"  \033[33m🤖 {tc.id} → {getattr(verdict, 'value', verdict)}{_c}{_cat}\033[0m")
            else:
                # No judge ran, print step-level status
                if verdict == ExecutionVerdict.PASS:
                    typer.echo(f"  \033[32m✅ {tc.id} → PASS\033[0m")
                elif verdict == ExecutionVerdict.FAIL:
                    typer.echo(f"  \033[31m❌ {tc.id} → FAIL\033[0m")
                elif status == ExecutionStatus.EXECUTED:
                    typer.echo(f"  \033[32m✅ {tc.id} → EXECUTED\033[0m")
                elif status == ExecutionStatus.FAILED:
                    typer.echo(f"  \033[31m❌ {tc.id} → FAILED\033[0m")
                else:
                    typer.echo(f"  \033[33m{tc.id} → {getattr(status, 'value', status)}\033[0m")

            # 6. Print token usage for this TC
            try:
                token_tracker.print_tc_summary()
            except Exception as exc:
                typer.echo(f"  [Token print error: {exc}]")

        except Exception as exc:
            typer.echo(f"  [Evaluation error for {tc.id}: {exc}]")

    def _on_tc_start(tc):
        """Set token tracker context before TC execution begins."""
        token_tracker.set_current_tc(tc.id)

    engine = ExecutionEngine(
        config,
        llm_provider=llm_provider,
        app_skill_context=skill_full_content,
        skill_hard_rules=skill_hard_rules,
        skill_app_name=skill_app_name or "",
        skill_user_intent=requirement,
        toggle_groups=skill_toggle_groups,
        on_tc_start=_on_tc_start,
        on_tc_complete=_on_tc_judged,
        token_tracker=token_tracker,
    )
    executed_tcs = await engine.execute_all(test_cases)
    was_interrupted = engine._interrupted

    # ── Phase 4b: Generate regression scripts for marked TCs ────────────
    _generate_regression_scripts(test_cases, config, output_dir, app_name=skill_app_name or "")

    # ── Teardown: kill app and close session after all TCs ───────────────
    try:
        await engine._teardown_app()
        typer.echo("  App stopped and session closed.")
    except Exception as exc:
        typer.echo(f"  [Teardown warning: {exc}]")

    # ── Phase 5: Evaluation + Judgment (runs per-TC during execution) ─────
    # Note: Per-TC evaluation and CaseJudgeAgent judgment happen inside the
    # on_tc_complete callback (see _on_tc_judged below), NOT in a separate loop.
    # executed_tcs are already evaluated by the time execute_all() returns.

    # ── Phase 5.5: Retry failed cases ──────────────────────────────────────
    # Filter: only retry FAIL cases that are NOT BUG (BUG failures are real defects)
    failed_tcs = [
        tc for tc in executed_tcs
        if tc.execution.verdict == "FAIL" and tc.execution.failure_category != "BUG"
    ]
    bug_tcs = [
        tc for tc in executed_tcs
        if tc.execution.verdict == "FAIL" and tc.execution.failure_category == "BUG"
    ]
    need_review_tcs = [tc for tc in executed_tcs if tc.execution.verdict == "NEED_REVIEW"]
    if bug_tcs:
        typer.echo(f"  [Skipping retry of {len(bug_tcs)} BUG case(s) — reported as defects]")
    if need_review_tcs:
        typer.echo(f"  [Skipping retry of {len(need_review_tcs)} NEED_REVIEW case(s) — will be in report for manual review]")
    if was_interrupted and failed_tcs:
        typer.echo(f"  [Interrupted — skipping retry of {len(failed_tcs)} failed case(s)]")
    if failed_tcs and not was_interrupted:
        typer.echo(f"  Retrying {len(failed_tcs)} failed case(s)...")
        retry_engine = ExecutionEngine(
            config,
            llm_provider=llm_provider,
            app_skill_context=skill_full_content,
            skill_hard_rules=skill_hard_rules,
            skill_app_name=skill_app_name or "",
            skill_user_intent=requirement,
        )
        for tc in failed_tcs:
            # Save first attempt data before clearing
            first_attempt = {
                "verdict": tc.execution.verdict.value if hasattr(tc.execution.verdict, 'value') else str(tc.execution.verdict) if tc.execution.verdict else "FAIL",
                "error_message": tc.execution.error_message or "",
                "failed_step": tc.execution.failed_step,
                "failure_type": tc.execution.failure_type.value if tc.execution.failure_type else None,
                "steps_count": len(tc.execution.steps),
                "duration_ms": tc.execution.duration_ms,
            }
            tc.execution.previous_attempts.append(first_attempt)

            # Reset execution state for retry
            tc.execution.status = ExecutionStatus.PENDING
            tc.execution.verdict = None
            tc.execution.steps = []
            tc.execution.error_message = ""
            tc.execution.retries += 1

        retried_tcs = await retry_engine.execute_all(failed_tcs)

        # Re-evaluate retried cases (step-level + judge)
        for tc in retried_tcs:
            evaluation = evaluator.evaluate(tc)
            tc.execution.verdict = evaluation.verdict
            tc.execution.confidence = evaluation.confidence
            tc.execution.reason = evaluation.reason

            # CaseJudgeAgent for retried TCs
            if judge is not None:
                needs_judge, level = should_invoke_judge(tc)
                if needs_judge:
                    try:
                        judge_result = await judge.evaluate(tc, level)
                        tc.execution.verdict = judge_result.verdict
                        tc.execution.confidence = judge_result.confidence
                        tc.execution.reason = judge_result.reasoning or judge_result.failure_root_cause
                        tc.execution.failure_category = judge_result.failure_category
                        tc.execution.failure_root_cause = judge_result.failure_root_cause
                        tc.execution.judge_evidence = judge_result.evidence
                        tc.execution.judge_confidence = judge_result.confidence
                        tc.execution.judge_reasoning = judge_result.reasoning
                    except Exception as exc:
                        typer.echo(f"  [Judge error for {tc.id}: {exc}]")

            # Print verdict with color
            verdict = tc.execution.verdict
            if hasattr(verdict, 'value'):
                verdict_str = verdict.value
            else:
                verdict_str = str(verdict) if verdict else "UNKNOWN"
            if verdict == ExecutionVerdict.PASS:
                typer.echo(f"  \033[32m🤖 {tc.id} → PASS (retry)\033[0m")
            elif verdict == ExecutionVerdict.FAIL:
                typer.echo(f"  \033[31m❌ {tc.id} → FAIL (retry)\033[0m")
            elif verdict == ExecutionVerdict.NEED_REVIEW:
                typer.echo(f"  \033[33m🤖 {tc.id} → NEED_REVIEW (retry)\033[0m")
            else:
                typer.echo(f"  {tc.id} → {verdict_str} (retry)")

        # Count results
        retry_passed = sum(1 for tc in retried_tcs if tc.execution.verdict == ExecutionVerdict.PASS)
        retry_failed = len(retried_tcs) - retry_passed
        typer.echo(f"  Retry results: {retry_passed} passed, {retry_failed} still failed")

        # Teardown retry engine
        try:
            await retry_engine._teardown_app()
        except Exception:
            pass

    # ── Phase 6: Overall evaluation + report generation ─────────────────────
    typer.echo("Generating overall evaluation and report...")
    overall_evaluator = OverallEvaluator()
    overall = overall_evaluator.evaluate(executed_tcs)

    report_gen = ReportGenerator(output_dir)
    report_path = report_gen.generate(name, executed_tcs, overall, config)

    typer.echo(f"Report generated: {report_path}")

    # ── Token usage summary and chart ──────────────────────────────────
    try:
        token_tracker.print_global_summary()
        chart_path = token_tracker.generate_chart(output_dir)
        if chart_path:
            typer.echo(f"  Token chart: {chart_path}")
    except Exception as exc:
        typer.echo(f"  [Token chart generation failed: {exc}]")

    # ── Cleanup checkpoint only on successful (non-interrupted) completion ──
    if not was_interrupted:
        ckpt.delete()

    # ── Phase 6b: Auto-capture failed cases for replay ──────────────────
    try:
        import uuid as _uuid
        from testagent.db.engine import get_session
        from testagent.db.repository import FailedReplayRepository
        from testagent.plan.replay_manager import capture_failures

        run_id = _uuid.uuid4().hex[:12]
        async with get_session() as session:
            replay_repo = FailedReplayRepository(session)
            await capture_failures(
                executed_tcs=executed_tcs,
                run_id=run_id,
                app_id=app_id or "unknown",
                report_path=str(report_path),
                repository=replay_repo,
            )
            typer.echo(f"  Failed cases captured for replay (run_id={run_id})")
    except Exception as exc:
        typer.echo(f"  [Replay capture warning: {exc}]")

    return report_path, overall, executed_tcs


# ── Resume helpers ─────────────────────────────────────────────────────────


def _find_latest_checkpoint(base_dir: str = "") -> str:
    """Find the most recent checkpoint file in the reports directory.

    Scans ``{base_dir}/*/checkpoint.json`` and returns the directory path
    of the one with the most recent ``updated_at`` timestamp.

    Returns:
        Directory path containing the latest checkpoint, or empty string.
    """
    from testagent.plan.checkpoint import CheckpointManager

    if not base_dir:
        base_dir = str(Path.cwd() / "reports")

    reports_dir = Path(base_dir)
    if not reports_dir.is_dir():
        return ""

    latest_dir = ""
    latest_time = ""

    for checkpoint_file in reports_dir.glob("*/checkpoint.json"):
        try:
            mgr = CheckpointManager(checkpoint_file.parent)
            data = mgr.load()
            if data.updated_at > latest_time:
                latest_time = data.updated_at
                latest_dir = str(checkpoint_file.parent)
        except Exception:
            continue

    return latest_dir


async def _resume_plan(
    resume_dir: str,
    llm_provider: Any = None,
    log_fn: Any = None,
) -> tuple[str | None, OverallEvaluation | None, list[TestCase]]:
    """Resume an interrupted plan from its checkpoint.

    Loads the checkpoint, skips completed TCs, re-executes interrupted ones,
    then runs evaluation and report generation on the merged results.
    """
    from testagent.plan.checkpoint import (
        CheckpointManager,
        CheckpointCorruptedError,
        CheckpointNotFoundError,
    )

    _log = log_fn or (lambda msg: None)

    # Resolve 'latest'
    if resume_dir == "latest":
        resume_dir = _find_latest_checkpoint()
        if not resume_dir:
            _log("Error: no checkpoint found in reports/ directory.")
            return None, None, []

    output_dir = resume_dir
    if not Path(output_dir).is_dir():
        _log(f"Error: output directory not found: {output_dir}")
        return None, None, []

    ckpt = CheckpointManager(output_dir)
    if not ckpt.exists():
        _log("Error: no checkpoint found in this directory.")
        return None, None, []

    try:
        data = ckpt.load()
    except CheckpointCorruptedError as exc:
        _log(f"Error: checkpoint file is corrupted: {exc}")
        _log("Delete checkpoint.json and start a fresh run.")
        return None, None, []

    completed_tcs, remaining_tcs = ckpt.load_and_resume()

    _log(f"Resuming plan '{data.plan_name}'")
    _log(f"  Already completed: {len(completed_tcs)}/{data.total_count}")
    _log(f"  Remaining: {len(remaining_tcs)}")

    # Reconstruct config from checkpoint
    from testagent.plan.models import PlanConfig

    config = PlanConfig(**data.config_snapshot)
    config.output_dir = output_dir

    was_interrupted = False

    if not remaining_tcs:
        _log("All test cases already completed. Generating report...")
        all_tcs = completed_tcs
    else:
        # Initialize LLM provider for execution
        from testagent.config.settings import get_settings
        from testagent.llm.local_provider import LLMProviderFactory

        settings = get_settings()
        if llm_provider is None:
            llm_provider = LLMProviderFactory.create(settings)

        # Execute remaining TCs
        _log(f"Executing {len(remaining_tcs)} remaining test cases...")

        from testagent.common.appium_manager import ensure_appium_running

        actual_url = await ensure_appium_running(
            udid=config.device_udid, appium_url=config.appium_url,
        )
        if not actual_url:
            _log("Error: Appium server is not available.")
            return None, None, completed_tcs + remaining_tcs
        if actual_url != config.appium_url:
            _log(f"  [Appium: {actual_url}]")
            config.appium_url = actual_url

        engine = ExecutionEngine(
            config,
            llm_provider=llm_provider,
            on_tc_complete=lambda tc: ckpt.save(
                data.plan_name, config, completed_tcs + remaining_tcs
            ),
        )
        executed_remaining = await engine.execute_all(remaining_tcs)
        was_interrupted = engine._interrupted

        # Teardown
        try:
            await engine._teardown_app()
        except Exception:
            pass

        all_tcs = completed_tcs + executed_remaining

    # ── Evaluation + report ────────────────────────────────────────────
    _log("Evaluating test case results...")
    evaluator = PerTCEvaluator()
    for tc in all_tcs:
        if tc.execution.status in (ExecutionStatus.EXECUTED, ExecutionStatus.FAILED):
            evaluation = evaluator.evaluate(tc)
            tc.execution.verdict = evaluation.verdict
            tc.execution.confidence = evaluation.confidence

    _log("Generating overall evaluation and report...")
    overall_evaluator = OverallEvaluator()
    overall = overall_evaluator.evaluate(all_tcs)

    report_gen = ReportGenerator(output_dir)
    report_path = report_gen.generate(data.plan_name, all_tcs, overall, config)

    _log(f"Report generated: {report_path}")

    # Cleanup checkpoint only on successful completion
    if not was_interrupted:
        ckpt.delete()

    return report_path, overall, all_tcs


async def run_single_plan(
    requirement: str,
    platform: str = "android",
    app_package: str = "",
    app_activity: str = "",
    app_id: str = "",
    device_udid: str = "",
    appium_url: str = "http://localhost:4723",
    auto_yes: bool = True,
    name: str = "",
    log_fn: Any = None,
    resume_dir: str = "",
    element_source: str = "multimodal",
) -> PlanResult:
    """Execute a single requirement document's full test lifecycle.

    This is the async entry point used by the batch orchestrator (ask command).
    It wraps ``_plan_command_async`` with structured result handling.

    Args:
        requirement: File path, URL, or raw requirement text.
        platform: Target platform ("android" or "ios").
        app_package: App identifier (Android→packageName, iOS→bundleId).
        app_activity: [Android only] App launch activity.
        app_id: App identifier for App Context Memory.
        device_udid: Target device serial (e.g. emulator-5554).
        appium_url: Appium server URL (e.g. http://localhost:4723).
        auto_yes: Skip interactive confirmation (always True for batch).
        name: Custom plan name.
        log_fn: Optional callable(str) for progress messages.

    Returns:
        PlanResult with status, summary, and report path.
    """
    import time

    _log = log_fn or (lambda msg: None)

    _log(f"Starting plan for: {requirement[:80]}...")
    start_time = time.monotonic()

    try:
        report_path, overall, executed_tcs = await _plan_command_async(
            requirement,
            name=name,
            platform=platform,
            app_package=app_package,
            app_activity=app_activity,
            app_id=app_id,
            device_udid=device_udid,
            appium_url=appium_url,
            auto_yes=auto_yes,
            resume_dir=resume_dir,
            element_source=element_source,
        )
    except Exception as exc:
        duration_s = time.monotonic() - start_time
        _log(f"Plan failed: {exc}")
        return PlanResult(
            status="failed",
            requirement_source=requirement,
            error=str(exc),
            duration=f"{duration_s:.1f}s",
        )

    duration_s = time.monotonic() - start_time

    if report_path is None:
        return PlanResult(
            status="failed",
            requirement_source=requirement,
            error="Plan was aborted (no test cases generated or user cancelled)",
            duration=f"{duration_s:.1f}s",
        )

    # Extract stats from OverallEvaluation (avoids regex parsing of Chinese report)
    total = overall.total_count if overall else len(executed_tcs)
    passed = overall.passed_count if overall else 0
    aborted_count = sum(1 for tc in executed_tcs if tc.execution.status == ExecutionStatus.ABORTED)
    failed = total - passed - aborted_count
    summary_parts = [f"{total} test cases: {passed} passed, {failed} failed"]
    if aborted_count:
        summary_parts.append(f"{aborted_count} aborted (not run)")
    summary_parts.append(f"Report: {report_path}")
    summary_lines = summary_parts

    _log(f"Plan completed: {', '.join(summary_lines)}")

    return PlanResult(
        status="completed",
        requirement_source=requirement,
        test_cases=executed_tcs,
        report_path=report_path,
        summary="\n".join(summary_lines),
        case_count=total,
        passed=passed,
        failed=failed,
        aborted=aborted_count,
        duration=f"{duration_s:.1f}s",
    )


# ── Multi-device helpers ────────────────────────────────────────────────


def load_multi_device_config(config_path: str) -> list[DevicePlanAssignment]:
    """Load multi-device configuration from a YAML file.

    Expected format:
    ```yaml
    assignments:
      - device: "emulator-5554"
        plan: "plans/login.yaml"
      - device: "192.168.1.100:5555"
        plan: "plans/pay.yaml"
    ```
    """
    with open(config_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    assignments: list[DevicePlanAssignment] = []
    for entry in data.get("assignments", []):
        device = DeviceInfo(udid=entry["device"])
        assignments.append(DevicePlanAssignment(device=device, plan_path=entry["plan"]))
    return assignments


def interactive_device_menu(platform: str = "android") -> list[DevicePlanAssignment]:
    """Interactive prompt to assign plans to devices.

    Flow:
    1. Display discovered devices.
    2. Let user assign a test plan YAML to each selected device.
    3. Return the assignments.

    TODO: Make this function platform-aware (currently only supports Android/adb).
    """
    import subprocess
    import typer

    typer.echo("\n🔍 正在扫描已连接设备...\n")
    try:
        result = subprocess.run(
            ["adb", "devices", "-l"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception as exc:
        typer.echo(f"❌ 无法运行 adb devices: {exc}")
        raise typer.Exit(1)

    devices: list[dict] = []
    for line in result.stdout.splitlines():
        if "device" not in line or "devices" in line:
            continue
        parts = line.strip().split()
        if len(parts) < 2:
            continue
        udid, status = parts[0], parts[1]
        if status != "device":
            continue
        name = udid
        for token in parts[2:]:
            if token.startswith("model:"):
                name = token.split(":", 1)[1].replace("_", " ")
                break
        devices.append({"udid": udid, "name": name, "index": len(devices) + 1})

    if not devices:
        typer.echo("❌ 未发现已连接的设备。请确认 USB 连接或模拟器已启动。")
        raise typer.Exit(1)

    typer.echo(f"发现 {len(devices)} 台设备:\n")
    for d in devices:
        typer.echo(f"  [{d['index']}] {d['name']:20s} ({d['udid']:30s}) ✅ 在线")
    typer.echo()

    selection = typer.prompt(
        "请选择要使用的设备 (多选用逗号分隔, 或按 Enter 全选)",
        default=",".join(str(d["index"]) for d in devices),
    )
    selected_indices = [int(s.strip()) for s in selection.split(",") if s.strip()]

    selected_devices = [d for d in devices if d["index"] in selected_indices]
    if not selected_devices:
        typer.echo("❌ 未选择任何设备。")
        raise typer.Exit(1)

    from pathlib import Path
    plan_dir = Path("plans")
    available_plans = list(plan_dir.glob("*.yaml")) + list(plan_dir.glob("*.yml"))
    if not available_plans:
        typer.echo("⚠️ 未在 plans/ 目录下找到测试计划文件。直接输入路径。")
        available_plans = []

    assignments: list[DevicePlanAssignment] = []
    typer.echo()
    for d in selected_devices:
        typer.echo(f"📋 为 {d['name']} ({d['udid']}) 选择测试计划:")
        if available_plans:
            for i, p in enumerate(available_plans, 1):
                typer.echo(f"    [{i}] {p.name}")
            choice = typer.prompt("  请输入编号或直接输入路径", default="1")
            try:
                idx = int(choice) - 1
                plan_path = str(available_plans[idx])
            except (ValueError, IndexError):
                plan_path = choice
        else:
            plan_path = typer.prompt("  请输入测试计划路径")

        device_info = DeviceInfo(udid=d["udid"], name=d["name"])
        assignments.append(DevicePlanAssignment(device=device_info, plan_path=plan_path))

    return assignments


async def run_multi_device_plan(
    config: str | None = None,
    log_fn: Any = None,
) -> PlanResult:
    """Run multiple test plans across multiple devices in parallel."""
    from testagent.common.appium_manager import AppiumManager
    from testagent.plan.port_allocator import PortAllocator

    _log = log_fn or (lambda msg: None)

    if config:
        assignments = load_multi_device_config(config)
    else:
        assignments = interactive_device_menu()

    if not assignments:
        return PlanResult(status="failed", error="No device-plan assignments")

    # Assign ports and start per-device Appium servers
    allocator = PortAllocator()
    appium_mgr = AppiumManager()
    _log(f"Preparing {len(assignments)} device(s)...")
    for a in assignments:
        port_pair = allocator.allocate()
        a.device.appium_url = f"http://localhost:{port_pair.appium_port}"
        a.device.system_port = port_pair.system_port
        try:
            await appium_mgr.ensure_appium_running(
                udid=a.device.udid, port=port_pair.appium_port,
            )
            _log(f"  Appium ready for {a.device.udid} on port {port_pair.appium_port}")
        except Exception as exc:
            _log(f"  Appium start failed for {a.device.udid}: {exc}")
            await appium_mgr.stop_all()
            return PlanResult(status="failed", error=str(exc))

    pending_tcs: list[TestCase] = []
    results: dict[str, PlanResult] = {}

    def _run_engine(assignment: DevicePlanAssignment) -> tuple[str, PlanResult]:
        import asyncio
        udid = assignment.device.udid
        try:
            report_path, overall, executed_tcs = asyncio.run(
                _plan_command_async(
                    requirement=assignment.plan_path,
                    app_package="",
                    app_activity="",
                    auto_yes=True,
                    device_udid=udid,
                    appium_url=assignment.device.appium_url,
                    system_port=assignment.device.system_port,
                )
            )
            return udid, PlanResult(
                status="completed",
                requirement_source=assignment.plan_path,
                test_cases=executed_tcs,
                report_path=report_path or "",
                case_count=overall.total_count if overall else 0,
                passed=overall.passed_count if overall else 0,
            )
        except Exception as exc:
            return udid, PlanResult(status="failed", error=str(exc))

    with ThreadPoolExecutor(max_workers=len(assignments)) as executor:
        futures = {
            executor.submit(_run_engine, a): a.device.udid
            for a in assignments
        }
        for future in as_completed(futures):
            udid, result = future.result()
            results[udid] = result
            if result.test_cases:
                pending_tcs.extend(result.test_cases)

    await appium_mgr.stop_all()

    total = sum(r.case_count for r in results.values() if r.case_count)
    passed = sum(r.passed for r in results.values() if r.passed)
    failed = total - passed
    summary = f"Multi-device: {total} cases across {len(results)} devices: {passed} passed, {failed} failed"

    return PlanResult(
        status="completed",
        requirement_source="multi-device",
        test_cases=pending_tcs,
        summary=summary,
        case_count=total,
        passed=passed,
        failed=failed,
    )
