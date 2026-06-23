from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import typer


def _fix_win_console() -> None:
    """Force UTF-8 encoding on Windows consoles to display CJK correctly."""
    if sys.platform == "win32":
        import os as _os
        _os.environ["PYTHONUTF8"] = "1"
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass
        try:
            sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except Exception:
            pass


_fix_win_console()

# Load ALL .env vars into os.environ (pydantic-settings only reads TESTAGENT_ prefixed ones)
from dotenv import load_dotenv
load_dotenv()

# HuggingFace mirror for China network access
import os as _os
if "HF_ENDPOINT" not in _os.environ:
    _os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

from testagent.cli.app_cmd import app_typer as app_group
from testagent.cli.config_cmd import config_app
from testagent.cli.mcp_cmd import mcp_app
from testagent.cli.memory_cmd import memory_typer as memory_group
from testagent.cli.output import RichOutput
from testagent.cli.rag_cmd import rag_index, rag_query
from testagent.cli.skill_cmd import skill_app

app = typer.Typer(name="testagent", help="AI Testing Agent Platform")
_output = RichOutput()


@app.command()
def init(
    project: str = typer.Argument(help="Project name"),
    project_type: str = typer.Option("api", "--type", "-t", help="Project type (api, web, app, or combined)"),
) -> None:
    """Initialize a new test project."""
    project_path = Path.cwd() / project

    if project_path.exists():
        typer.echo(f"Project '{project}' already exists at {project_path}")
        raise typer.Exit(1)

    project_path.mkdir(parents=True)
    (project_path / "test-plans").mkdir()
    (project_path / "config").mkdir()

    config = {
        "version": "1.0",
        "project": project,
        "type": project_type,
        "env": {"default": "dev"},
        "skills": [],
    }

    import json

    (project_path / "testagent.json").write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    typer.echo(f"Initialized test project '{project}' at {project_path}")
    typer.echo(f"  Type: {project_type}")
    typer.echo(f"  Config: {project_path / 'testagent.json'}")
    typer.echo(f"  Plans:  {project_path / 'test-plans'}/")
    typer.echo(f"  Config: {project_path / 'config'}/")


@app.command()
def run(
    skill: str | None = typer.Option(None, "--skill", "-s", help="Skill name to execute"),
    plan: Path | None = typer.Option(None, "--plan", "-p", help="Path to test plan JSON file"),  # noqa: B008
    env: str = typer.Option("dev", "--env", "-e", help="Target environment"),
    url: str | None = typer.Option(None, "--url", "-u", help="Target URL (overrides env config)"),
) -> None:
    """Execute a test skill or plan."""
    if skill is None and plan is None:
        typer.echo("Either --skill or --plan must be provided.")
        raise typer.Exit(1)

    if plan is not None and not plan.exists():
        typer.echo(f"Plan file not found: {plan}")
        raise typer.Exit(1)

    plan_name = f"plan:{plan.name}" if plan else None
    timeout = "60s" if not url else "120s"
    _output.print_header(
        skill=skill or (plan_name or "unknown"),
        target=url or env,
        timeout=timeout,
    )

    try:
        from testagent.gateway.session import run_session
    except ImportError:
        typer.echo("Session execution module not available. Use 'testagent serve' to start the gateway first.")
        raise typer.Exit(1) from None

    import asyncio

    results = asyncio.run(
        run_session(
            skill_name=skill,
            plan_path=str(plan) if plan else None,
            env=env,
            url=url,
        )
    )

    tasks: list[dict[str, Any]] = results.get("tasks", [])
    passed = sum(1 for t in tasks if t.get("status") == "passed")
    failed = sum(1 for t in tasks if t.get("status") == "failed")

    for i, task in enumerate(tasks, 1):
        _output.print_task_result(i, len(tasks), task)

    _output.print_summary(passed, failed, results.get("duration", "-"))


@app.command()
def ask(
    query: str = typer.Argument(..., help="自然语言测试描述，如 '测试android搜索框'"),
) -> None:
    """用自然语言描述，让 Agent 自动探索并执行测试。"""
    import asyncio

    from testagent.cli.ask import execute_natural_language

    typer.echo("  TestAgent 自然语言测试")
    typer.echo("  " + "-" * 50)

    try:
        result = asyncio.run(execute_natural_language(query))
    except Exception as exc:
        typer.echo(f"  ! 执行失败: {exc}")
        raise typer.Exit(1) from exc

    status = result.get("status", "unknown")
    if status == "failed":
        typer.echo(f"\n  ! 失败: {result.get('error', 'unknown error')}")
        raise typer.Exit(1)

    typer.echo("  " + "-" * 50)
    typer.echo(f"  状态:    {status}")
    typer.echo(f"  耗时:    {result.get('duration', '-')}")
    typer.echo(f"  轮次:    {result.get('message_count', 0)}")

    summary = result.get("summary", "")
    if summary:
        typer.echo(f"\n  测试报告:\n  {summary}")

    typer.echo("")


@app.command()
def chat() -> None:
    """交互式自然语言测试模式 — 像聊天一样测试 App。"""
    import asyncio

    from testagent.cli.ask import interactive_chat

    asyncio.run(interactive_chat())


app.add_typer(config_app)
app.add_typer(skill_app)
app.add_typer(mcp_app)
app.add_typer(app_group)  # testagent app plan, etc.
app.add_typer(memory_group)  # testagent memory list-patterns, approve, etc.


@app.command()
def ci(
    skill: str = typer.Argument(help="Skill name to run in CI mode"),
    exit_code: bool = typer.Option(False, "--exit-code", help="Return non-zero exit code on failure"),
    junit: Path | None = typer.Option(None, "--junit", "-j", help="Path to output JUnit XML report", exists=False),  # noqa: B008
    timeout: int = typer.Option(300, "--timeout", "-t", help="Global timeout in seconds", min=1),
    env: str = typer.Option("ci", "--env", "-e", help="Target environment"),
    url: str | None = typer.Option(None, "--url", "-u", help="Target URL (overrides env config)"),
) -> None:
    """Run a skill in CI mode (non-interactive).

    Designed for CI/CD pipelines. Supports JUnit XML report output,
    configurable timeout, and non-zero exit code on failure.
    """
    if timeout <= 0:
        typer.echo("Timeout must be a positive integer.")
        raise typer.Exit(1)

    try:
        from testagent.gateway.session import run_session
    except ImportError:
        typer.echo("Session execution module not available. Use 'testagent serve' to start the gateway first.")
        raise typer.Exit(1) from None

    import asyncio

    _output.print_header(skill=skill, target=url or env, timeout=f"{timeout}s")

    import time

    start_time = time.monotonic()

    try:
        results = asyncio.run(run_session(skill_name=skill, env=env, url=url))
    except TimeoutError:
        typer.echo(f"CI run timed out after {timeout}s")
        timeout_error = {
            "name": skill,
            "status": "error",
            "duration": str(timeout),
            "error": "Global timeout exceeded",
        }
        _write_junit_report([timeout_error], junit)
        raise typer.Exit(1) from None

    elapsed = time.monotonic() - start_time
    tasks: list[dict[str, Any]] = results.get("tasks", [])

    for i, task in enumerate(tasks, 1):
        _output.print_task_result(i, len(tasks), task)

    passed = sum(1 for t in tasks if t.get("status") == "passed")
    failed = sum(1 for t in tasks if t.get("status") == "failed")
    duration = results.get("duration", f"{elapsed:.1f}s")
    _output.print_summary(passed, failed, duration)

    _write_junit_report(tasks, junit)

    if exit_code and failed > 0:
        raise typer.Exit(1)


def _write_junit_report(tasks: list[dict[str, Any]], path: Path | None) -> None:
    if path is None:
        return
    from testagent.cli.junit import generate_junit_xml

    xml_content = generate_junit_xml(tasks)
    path.write_text(xml_content, encoding="utf-8")
    typer.echo(f"JUnit report written to {path}")


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", help="Bind host"),
    port: int = typer.Option(8000, "--port", "-p", help="Bind port"),
) -> None:
    """Start the TestAgent Gateway server."""
    import uvicorn

    typer.echo(f"Starting TestAgent Gateway on {host}:{port}")
    uvicorn.run("testagent.gateway.app:app", host=host, port=port, log_level="info")


app.command(name="rag-index")(rag_index)
app.command(name="rag-query")(rag_query)


@app.command()
def help() -> None:
    """显示所有可用命令及用法。"""
    typer.echo("TestAgent — AI 测试自动化平台")
    typer.echo("")
    typer.echo("用法: testagent <command> [options]")
    typer.echo("")
    typer.echo("── Android App 测试 (tpilot) ────────────────────")
    typer.echo("  testagent app tpilot plan <需求>    自动生成、执行测试用例并生成报告")
    typer.echo("    -p, --app-package               App 包名 (如 com.example.app)")
    typer.echo("    -a, --app-activity              启动 Activity")
    typer.echo("    -n, --name                      自定义计划名称")
    typer.echo("    -y, --auto-yes                  跳过确认，直接执行")
    typer.echo("    -r, --resume <path|latest>      恢复中断的计划")
    typer.echo("    -m, --multi-config <yaml>       多设备并行测试配置文件路径")
    typer.echo("")
    typer.echo("  testagent app tpilot replay        重跑之前失败用例（生成 delta 报告）")
    typer.echo("")
    typer.echo("  ── 多设备并行测试（3 种使用方式） ──")
    typer.echo("  [1] 交互式菜单（推荐）:")
    typer.echo("    testagent app tpilot plan")
    typer.echo("    → 自动扫描 adb devices → 选择设备 → 分配计划 → 并行执行")
    typer.echo("")
    typer.echo("  [2] YAML 配置文件:")
    typer.echo("    testagent app tpilot plan -m configs/multi_device.yaml")
    typer.echo("")
    typer.echo("  [3] 传统模式（单设备）:")
    typer.echo('    testagent app tpilot plan "需求文档" -p com.example.app -y')
    typer.echo("")
    typer.echo("── 自然语言测试 ──────────────────────────────")
    typer.echo("  testagent ask <描述>         用自然语言描述测试，Agent 自动执行")
    typer.echo("  testagent chat               交互式自然语言测试模式")
    typer.echo("")
    typer.echo("── 配置命令 ──────────────────────────────────────")
    typer.echo("  testagent config show        查看当前 API 配置（Key 隐藏）")
    typer.echo("  testagent config show -s     查看当前 API 配置（显示完整 Key）")
    typer.echo("  testagent config configure   交互式配置 LLM / Vision API")
    typer.echo("  testagent config help        查看配置命令详细用法")
    typer.echo("")
    typer.echo("── 其他命令 ──────────────────────────────────────")
    typer.echo("  testagent init <项目名>      初始化测试项目")
    typer.echo("  testagent run -s <skill>     执行指定测试 skill")
    typer.echo("  testagent ci <skill>         CI 模式运行（支持 JUnit 报告）")
    typer.echo("  testagent serve              启动 TestAgent Gateway 服务")
    typer.echo("  testagent rag-index          构建 RAG 知识库索引")
    typer.echo("  testagent rag-query <query>  查询 RAG 知识库")
    typer.echo("")
    typer.echo("── 未来扩展（预留）──────────────────────────────")
    typer.echo("  testagent app web ...         Web 浏览器测试（规划中）")
    typer.echo("  testagent app api ...         API 接口测试（规划中）")
    typer.echo("")
    typer.echo("── app tpilot plan 确认与编辑流程 ─────────────")
    typer.echo("  AI 生成测试用例后，会显示用例列表并提示:")
    typer.echo("    [y] 执行所有用例 — 直接运行全部用例")
    typer.echo("    [e] 编辑用例     — 进入编辑模式（见下方）")
    typer.echo("    [n] 取消         — 放弃本次执行")
    typer.echo("")
    typer.echo("  编辑模式菜单:")
    typer.echo("    [a] 添加用例 — 手动创建新用例，依次输入:")
    typer.echo("         用例 ID (如 TC-NEW-001)")
    typer.echo("         用例标题")
    typer.echo("         优先级 (P0/P1/P2，默认 P1)")
    typer.echo("         是否为核心用例 (y/n)")
    typer.echo("         关联需求 ID (逗号分隔，可留空)")
    typer.echo("         步骤: 逐条输入 action / target / value")
    typer.echo("         action 类型: tap, type, swipe, assert, launch, exec, screenshot, wait")
    typer.echo("")
    typer.echo("    [d] 删除用例 — 显示用例列表，输入编号删除")
    typer.echo("    [m] 修改用例 — 选择用例后可修改:")
    typer.echo("         [t] 标题    [p] 优先级    [c] 核心标记    [s] 步骤")
    typer.echo("         步骤编辑支持添加和删除单个步骤")
    typer.echo("    [b] 返回 — 回到确认界面")
    typer.echo("")
    typer.echo("示例:")
    typer.echo('  testagent app tpilot plan "测试首页功能" -p com.example.app -y')
    typer.echo('  testagent app tpilot plan -m configs/multi_device.yaml')
    typer.echo('  testagent config configure --llm-api-key "sk-xxx" --vision-api-key "ark-xxx"')
    typer.echo('  testagent ask "测试登录功能"')
    typer.echo("")
    typer.echo("详细帮助: testagent <command> --help")
