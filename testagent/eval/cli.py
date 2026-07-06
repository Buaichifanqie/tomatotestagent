"""CLI sub-commands for the eval subsystem.

Usage::

    testagent eval list
    testagent eval run <suite_name>
    testagent eval history
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.table import Table

from testagent.eval.loader import discover_suites, load_suite
from testagent.eval.reports.json_reporter import JsonReporter
from testagent.eval.reports.markdown_reporter import MarkdownReporter
from testagent.eval.runner import EvalRunner
from testagent.eval.generator import (
    detect_app_package,
    get_app_activity,
    read_skill_context,
    explore_app_pages,
    generate_tasks_with_llm,
    write_task_files,
)

eval_app = typer.Typer(name="eval", help="AI Agent 评测")
console = Console()
_DEFAULT_REPORT_DIR = Path("reports") / "eval"


# ── Helper ──────────────────────────────────────────────────────────────────────


def _collect_mcp_tools() -> list[dict[str, Any]]:
    """Collect MCP tools for the agent (inner function dict — provider adds wrapper).

    The OpenAIProvider.chat() at line 96 wraps each tool as:
        {"type": "function", "function": t}
    so we return only the inner ``function`` dict with name/description/parameters.
    """
    return [
        {
            "name": "screenshot",
            "description": "Take a screenshot of the current screen. Use this to see what's on the screen before taking action.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "tap",
            "description": "Tap at specific screen coordinates (x, y). Screen coordinates are typically 0-1080 for width and 0-2400 for height.",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "integer", "description": "X coordinate"},
                    "y": {"type": "integer", "description": "Y coordinate"},
                },
                "required": ["x", "y"],
            },
        },
        {
            "name": "type_text",
            "description": "Type text into the currently focused input field.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "The text to type"},
                },
                "required": ["text"],
            },
        },
        {
            "name": "swipe",
            "description": "Swipe from one coordinate to another.",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_x": {"type": "integer"}, "start_y": {"type": "integer"},
                    "end_x": {"type": "integer"}, "end_y": {"type": "integer"},
                },
                "required": ["start_x", "start_y", "end_x", "end_y"],
            },
        },
        {
            "name": "get_page_source",
            "description": "Get the current screen's UI structure as XML. Use this to find UI elements and their positions.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        {
            "name": "launch_app",
            "description": "Launch an Android app by package name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "package": {"type": "string", "description": "Android package name"},
                },
                "required": ["package"],
            },
        },
    ]


def _resolve_output_dir(suite_name: str, run_id: str, output: str = "") -> Path:
    """Determine the output directory for reports."""
    if output:
        return Path(output)
    return _DEFAULT_REPORT_DIR / suite_name / run_id


# ── Commands ────────────────────────────────────────────────────────────────────


@eval_app.command()
def run(
    suite_name: str = typer.Argument(..., help="套件名称或路径"),
    trials: int = typer.Option(0, "--trials", "-t", help="覆盖默认试次数（0=使用YAML定义）"),
    filter: str = typer.Option("", "--filter", "-f", help="按任务ID过滤（glob模式）"),  # noqa: A002
    device: str = typer.Option("emulator-5554", "--device", "-d", help="Android设备序列号（adb devices）"),
    appium_url: str = typer.Option("http://localhost:4723", "--appium-url", "-u", help="Appium服务器地址"),
    output: str = typer.Option("", "--output", "-o", help="报告输出目录"),
) -> None:
    """运行评测套件。"""
    # 1. Load suite
    console.print(f"[bold]Loading suite:[/bold] {suite_name}")
    try:
        suite = load_suite(suite_name)
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]Error loading suite:[/red] {exc}")
        raise typer.Exit(1) from exc

    # 2. Apply task filter
    if filter:
        original_count = len(suite.tasks)
        suite.tasks = [t for t in suite.tasks if fnmatch.fnmatch(t.id, filter)]
        console.print(
            f"  Filter applied: {original_count} -> [cyan]{len(suite.tasks)}[/cyan] tasks matched '{filter}'"
        )
        if not suite.tasks:
            console.print("[yellow]No tasks match the filter. Nothing to run.[/yellow]")
            raise typer.Exit(0)

    # 3. Override trials if specified
    if trials > 0:
        console.print(f"  Overriding trials: {suite.default_trials} -> [cyan]{trials}[/cyan]")
        for task in suite.tasks:
            task.trials = trials

    # 4. Initialize LLM provider + MCP tools
    console.print("  Initializing LLM provider ...")
    try:
        from testagent.config.settings import get_settings
        from testagent.llm.openai_provider import OpenAIProvider

        settings = get_settings()
        llm = OpenAIProvider(settings)
    except Exception as exc:
        console.print(f"[red]Failed to initialize LLM provider:[/red] {exc}")
        raise typer.Exit(1) from exc

    # 5. Create Appium session via direct HTTP POST + dispatch function
    console.print("  Creating Appium session ...")
    from testagent.mcp_servers.appium_server.tools import (
        app_tap, app_screenshot, app_type_text, app_swipe,
        app_get_source, app_launch,
    )

    android_sdk = r"C:\Users\kongwenshuo\AppData\Local\Android\Sdk"
    # Appium driver checks these at session creation time
    import os as _os
    if not _os.environ.get("ANDROID_HOME") and _os.path.isdir(android_sdk):
        _os.environ["ANDROID_HOME"] = android_sdk
        _os.environ["ANDROID_SDK_ROOT"] = android_sdk

    async def _init_session() -> tuple[str, dict[str, Any]]:
        """Create Appium session and return (session_id, caps)."""
        caps: dict[str, Any] = {
            "platformName": "Android",
            "appium:automationName": "UiAutomator2",
            "appium:deviceName": device,
            "appium:udid": device,
            "appium:noReset": True,
            "appium:autoGrantPermissions": True,
            "appium:newCommandTimeout": 300,
            "appium:allowInsecure": "*:adb_shell",
            "appium:systemPort": 8200,
        }
        if android_sdk:
            caps["appium:androidHome"] = android_sdk
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{appium_url}/session",
                json={"capabilities": {"alwaysMatch": caps, "firstMatch": [{}]}},
            )
            data = resp.json()
        if resp.status_code == 200 and "value" in data:
            sid: str = data["value"].get("sessionId") or data.get("sessionId", "")
            return sid, caps
        err = data.get("value", {}).get("message", data.get("message", str(resp.status_code)))
        raise RuntimeError(f"Appium session creation failed: {err}")

    try:
        session_id, _ = asyncio.run(_init_session())
        console.print(f"  [green]Appium session created: {session_id[:12]}...[/green]")
    except httpx.TimeoutException:
        console.print("[red]Appium session creation timed out. Is Appium running?[/red]")
        raise typer.Exit(1)
    except Exception as exc:
        console.print(f"[red]Failed to create Appium session: {exc}[/red]")
        raise typer.Exit(1)

    # Force-launch Bilibili — try strategies until it works
    import time as _time
    pkg_name = "tv.danmaku.bili"
    pkg_activity = ".MainActivityV2"
    from testagent.common.adb_utils import adb_command
    try:
        adb_command(device, "shell", "am", "force-stop", pkg_name,
                    capture_output=True, timeout=10)
    except Exception:
        pass
    _time.sleep(1)
    for adb_args in [
        ("shell", "am", "start", "-n", f"{pkg_name}/{pkg_activity}"),
        ("shell", "monkey", "-p", pkg_name, "1"),
        ("shell", "am", "start", "-a", "android.intent.action.MAIN",
         "-c", "android.intent.category.LAUNCHER", pkg_name),
    ]:
        try:
            adb_command(device, *adb_args, capture_output=True, text=True, timeout=10)
            _time.sleep(2)
            focus = adb_command(device, "shell", "dumpsys", "window",
                                capture_output=True, text=True, timeout=5)
            if pkg_name in (focus.stdout or ""):
                console.print(f"  [green]App launched: {pkg_name}[/green]")
                break
        except Exception:
            continue
    else:
        console.print(f"  [yellow]Could not confirm {pkg_name} is in foreground[/yellow]")
    _time.sleep(3)

    async def dispatch_fn(tool_name: str, args: dict) -> dict:
        """Route tool calls to actual MCP implementations."""
        try:
            if tool_name == "screenshot":
                return await app_screenshot(appium_url=appium_url, session_id=session_id)
            elif tool_name == "tap":
                return await app_tap(x=args["x"], y=args["y"], appium_url=appium_url, session_id=session_id)
            elif tool_name == "type_text":
                return await app_type_text(text=args["text"], appium_url=appium_url, session_id=session_id)
            elif tool_name == "swipe":
                return await app_swipe(
                    start_x=args["start_x"], start_y=args["start_y"],
                    end_x=args["end_x"], end_y=args["end_y"],
                    appium_url=appium_url, session_id=session_id,
                )
            elif tool_name == "get_page_source":
                return await app_get_source(appium_url=appium_url, session_id=session_id)
            elif tool_name == "launch_app":
                pkg = args.get("package", "")
                activity = args.get("activity", "")
                force = args.get("force_stop", False)
                if not pkg:
                    return {"error": "Missing package name"}
                from testagent.common.adb_utils import adb_command
                if force:
                    try:
                        adb_command(device, "shell", "am", "force-stop", pkg,
                                    capture_output=True, timeout=10)
                    except Exception:
                        pass
                    await asyncio.sleep(1)
                try:
                    if activity:
                        component = f"{pkg}/{activity}" if not activity.startswith(".") else f"{pkg}{activity}"
                        adb_command(device, "shell", "am", "start", "-n", component,
                                    capture_output=True, timeout=10)
                    else:
                        adb_command(device, "shell", "am", "start",
                                    "-a", "android.intent.action.MAIN",
                                    "-c", "android.intent.category.LAUNCHER",
                                    pkg, capture_output=True, timeout=10)
                    await asyncio.sleep(3)
                    return {"result": "launched", "package": pkg}
                except Exception as e:
                    return await app_launch(package=pkg, appium_url=appium_url, session_id=session_id)
            else:
                return {"error": f"Unknown tool: {tool_name}"}
        except KeyError as e:
            return {"error": f"Missing required parameter: {e}"}
        except Exception as e:
            return {"error": f"{tool_name} failed: {e}"}

    mcp_tools = _collect_mcp_tools()
    model_name = settings.openai_model or "unknown"

    # 6. Run
    console.print(f"  Running [cyan]{len(suite.tasks)}[/cyan] tasks with [cyan]{suite.default_trials}[/cyan] trial(s) each ...")
    console.print("")

    try:
        eval_system_prompt = """你是一个手机 App 自动化测试 Agent。

可用工具：screenshot(截图), tap(x,y)(点击), type_text(text)(输入), swipe(滑动), get_page_source(获取UI文本), launch_app(启动App)

工作流程：
1. 先 get_page_source 看 UI 文字，再 screenshot 看画面
2. 执行操作（点击、输入、滑动）
3. 再次 get_page_source + screenshot 验证
4. 如果任务目标已达成，立即输出结论并 STOP（不要再操作）

关键：完成验证后立即停止！不需要多余操作。直接用中文输出结论。"""

        # Load app skill knowledge if available
        skill_context = ""
        if suite.app:
            skill_dir = Path(__file__).resolve().parent.parent.parent / "skills" / "apps" / suite.app
            skill_file = skill_dir / "SKILL.md"
            if skill_file.exists():
                skill_context = skill_file.read_text(encoding="utf-8")
                # Also include sub-skills
                for sub in sorted(skill_dir.glob("*.md")):
                    if sub.name != "SKILL.md":
                        skill_context += f"\n\n--- {sub.stem} ---\n" + sub.read_text(encoding="utf-8")
                # Limit to avoid token overflow
                if len(skill_context) > 6000:
                    skill_context = skill_context[:6000] + "\n...(truncated)"
                console.print(f"  [green]Loaded skill: {suite.app}[/green]")

        runner = EvalRunner(
            llm_provider=llm,
            mcp_tools=mcp_tools,
            dispatch_fn=dispatch_fn,
            system_prompt=eval_system_prompt,
            skill_context=skill_context,
            model_name=model_name,
            max_rounds=15,
        )
        result = asyncio.run(runner.run_suite(suite))
    except Exception as exc:
        console.print(f"[red]Execution failed:[/red] {exc}")
        raise typer.Exit(1) from exc
    finally:
        if session_id:
            try:
                httpx.delete(f"{appium_url}/session/{session_id}", timeout=10)
            except Exception:
                pass

    # 7. Save reports
    run_id = result.run_id
    output_dir = _resolve_output_dir(suite.name, run_id, output)

    md_path = MarkdownReporter.save(result, output_dir)
    json_path = JsonReporter.save(result, output_dir)
    console.print(f"  [green]Report saved:[/green] {md_path}")
    console.print(f"  [green]JSON saved:[/green] {json_path}")
    console.print("")

    # 8. Summary
    console.print("[bold]Summary:[/bold]")
    console.print(f"  Suite:     {result.suite_name}")
    console.print(f"  Tasks:     {len(result.task_results)}")
    console.print(f"  Duration:  {result.duration:.1f}s")
    console.print(f"  pass@1:    {result.pass_at_1_rate:.1%}")
    console.print(f"  pass@k:    {result.overall_pass_rate:.1%}")
    console.print(f"  all-pass:  {result.pass_k_rate:.1%}")

    total_trials = sum(len(tr.trials) for tr in result.task_results)
    passed_trials = sum(
        sum(1 for t in tr.trials if t.passed) for tr in result.task_results
    )
    console.print(f"  Trials:    {passed_trials}/{total_trials} passed")

    # Exit with non-zero if any task failed
    if result.overall_pass_rate < 1.0:
        raise typer.Exit(1)


@eval_app.command()
def generate(
    app_name: str = typer.Argument(..., help="要评测的 App 名称或包名"),
    device: str = typer.Option("emulator-5554", "--device", "-d", help="Android设备序列号"),
    appium_url: str = typer.Option("http://localhost:4723", "--appium-url", "-u", help="Appium服务器地址"),
    explore: bool = typer.Option(True, "--explore/--no-explore", help="是否探索 App 页面"),
) -> None:
    """自动生成评测任务套件 (Phase 2)。

    扫描设备上的 App，结合 SKILL.md 知识，用 LLM 自动生成 YAML 评测任务。
    """
    console.print(f"[bold]Generating eval suite for:[/bold] {app_name}")

    async def _generate_all() -> tuple[str, str, list[dict], list[dict]]:
        """Run the entire generation pipeline in a single async context."""
        from testagent.config.settings import get_settings
        from testagent.llm.openai_provider import OpenAIProvider
        settings = get_settings()
        llm = OpenAIProvider(settings)

        # 1. Detect package
        console.print("  Detecting app package...")
        pkg = await detect_app_package(app_name, device, llm)
        console.print(f"  [green]Matched: {pkg}[/green]")

        # 2. Get activity + skill
        activity = get_app_activity(device, pkg)
        console.print(f"  Activity: {activity}")
        skill = read_skill_context(app_name) or read_skill_context(pkg.split(".")[-1])
        if skill:
            console.print(f"  [green]Found SKILL.md ({len(skill)} chars)[/green]")
        else:
            console.print(f"  [yellow]No SKILL.md found[/yellow]")

        # 3. Optional exploration
        pages = []
        if explore:
            console.print("  Exploring app pages...")
            import os as _os
            android_sdk = r"C:\Users\kongwenshuo\AppData\Local\Android\Sdk"
            if not _os.environ.get("ANDROID_HOME") and _os.path.isdir(android_sdk):
                _os.environ["ANDROID_HOME"] = android_sdk
                _os.environ["ANDROID_SDK_ROOT"] = android_sdk
            try:
                import httpx
                from testagent.common.adb_utils import adb_command
                caps = {
                    "platformName": "Android",
                    "appium:automationName": "UiAutomator2",
                    "appium:deviceName": device,
                    "appium:udid": device,
                    "appium:noReset": True,
                    "appium:autoGrantPermissions": True,
                    "appium:newCommandTimeout": 300,
                    "appium:allowInsecure": "*:adb_shell",
                }
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(
                        f"{appium_url}/session",
                        json={"capabilities": {"alwaysMatch": caps, "firstMatch": [{}]}},
                    )
                    data = resp.json()
                    if resp.status_code == 200:
                        sid = data["value"].get("sessionId", "")
                        adb_command(device, "shell", "am", "force-stop", pkg,
                                    capture_output=True, timeout=10)
                        import time as _t; _t.sleep(1)
                        adb_command(device, "shell", "am", "start", "-n", f"{pkg}/{activity}",
                                    capture_output=True, timeout=10)
                        _t.sleep(3)
                        pages = await explore_app_pages(device, pkg, activity, appium_url, sid, llm)
                        await client.delete(f"{appium_url}/session/{sid}")
                console.print(f"  Explored {len(pages)} pages")
            except Exception as exc:
                console.print(f"  [yellow]Explore skipped: {exc}[/yellow]")

        # 4. Generate tasks
        console.print("  Generating tasks with LLM...")
        tasks = await generate_tasks_with_llm(llm, pkg, app_name, skill, pages)
        return pkg, app_name, tasks, pages

    # Run the entire pipeline
    try:
        pkg, name, tasks, _ = asyncio.run(_generate_all())
    except Exception as exc:
        console.print(f"[red]Generation failed: {exc}[/red]")
        raise typer.Exit(1)

    if not tasks:
        console.print("[red]LLM returned no tasks[/red]")
        raise typer.Exit(1)

    # Write YAML files
    output_dir = write_task_files(name, tasks, package=pkg)
    console.print(f"  [green]Generated {len(tasks)} tasks[/green]")
    console.print(f"  Output: {output_dir}")
    console.print(f"\n  Run with: testagent eval run {name}")


@eval_app.command()
def list() -> None:  # noqa: A001
    """列出所有可用评测套件。"""
    suites = discover_suites()

    if not suites:
        console.print("[yellow]未发现评测套件。[/yellow]")
        console.print("  在 [bold]evals/tasks/[/bold] 目录下创建 YAML 套件后即可显示。")
        return

    table = Table(title="可用评测套件")
    table.add_column("名称", style="cyan", no_wrap=True)
    table.add_column("描述")
    table.add_column("任务数", justify="right")

    for suite in suites:
        table.add_row(suite.name, suite.description, str(len(suite.tasks)))

    console.print(table)


@eval_app.command()
def history() -> None:
    """显示历史评测报告。"""
    report_dir = _DEFAULT_REPORT_DIR
    if not report_dir.exists():
        console.print(f"[yellow]未发现历史报告目录: {report_dir}[/yellow]")
        return

    reports: list[dict[str, Any]] = []
    for suite_dir in sorted(report_dir.iterdir()):
        if not suite_dir.is_dir():
            continue
        for run_dir in sorted(suite_dir.iterdir()):
            if not run_dir.is_dir():
                continue
            report_md = run_dir / "report.md"
            summary_json = run_dir / "summary.json"
            if report_md.is_file() or summary_json.is_file():
                reports.append({
                    "suite": suite_dir.name,
                    "run_id": run_dir.name,
                    "path": str(run_dir),
                })

    if not reports:
        console.print("[yellow]未发现历史评测报告。[/yellow]")
        return

    table = Table(title="历史评测报告")
    table.add_column("套件", style="cyan", no_wrap=True)
    table.add_column("运行ID")
    table.add_column("路径")

    for r in reports:
        table.add_row(r["suite"], r["run_id"], r["path"])

    console.print(table)
