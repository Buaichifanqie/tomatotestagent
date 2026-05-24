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

from testagent.cli.mcp_cmd import mcp_app
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


@app.command()
def plan(
    requirement: str = typer.Argument(
        ..., help="产品需求文档路径 或 自然语言需求描述"
    ),
    name: str = typer.Option("", "--name", "-n", help="自定义计划名称"),
    app_package: str = typer.Option("", "--app-package", "-p", help="App package name"),
    app_activity: str = typer.Option("", "--app-activity", "-a", help="App launch activity"),
    auto_yes: bool = typer.Option(
        False, "--auto-yes", "-y", help="跳过确认步骤，直接执行"
    ),
) -> None:
    """根据产品需求自动生成、执行测试用例并生成结构化报告。"""
    from testagent.cli.plan import plan_command as _plan_command

    _plan_command(
        requirement,
        name=name,
        app_package=app_package,
        app_activity=app_activity,
        auto_yes=auto_yes,
    )


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


app.add_typer(skill_app)
app.add_typer(mcp_app)
app.command(name="rag-index")(rag_index)
app.command(name="rag-query")(rag_query)
