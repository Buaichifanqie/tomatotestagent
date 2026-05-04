from __future__ import annotations

from pathlib import Path
from typing import Any

import typer

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
        from testagent.gateway.session import run_session  # type: ignore[attr-defined]
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
def chat() -> None:
    """Start an interactive testing chat session."""
    from testagent.agent.loop import agent_loop

    typer.echo("TestAgent Chat — type 'exit' to quit, 'help' for commands.")
    typer.echo("-" * 40)

    messages: list[dict[str, object]] = []

    while True:
        user_input = typer.prompt("You").strip()

        if user_input.lower() in ("exit", "quit"):
            typer.echo("Goodbye!")
            break

        if user_input.lower() == "help":
            typer.echo("Commands: exit/quit, help, clear")
            typer.echo("Or ask me anything about testing!")
            continue

        if user_input.lower() == "clear":
            messages.clear()
            typer.echo("Chat history cleared.")
            continue

        import asyncio

        from testagent.config.settings import get_settings
        from testagent.llm.openai_provider import OpenAIProvider

        system = "You are TestAgent, an AI testing assistant."
        llm = OpenAIProvider(get_settings())
        result = asyncio.run(agent_loop(messages, tools=[], system=system, llm_provider=llm))

        typer.echo(f"\nAgent: {result}")
        print()


@app.command()
def ci(
    skill: str = typer.Argument(help="Skill name to run in CI mode"),
    exit_code: bool = typer.Option(False, "--exit-code", "-e", help="Return non-zero exit code on failure"),
) -> None:
    """Run a skill in CI mode (non-interactive)."""
    try:
        from testagent.gateway.session import run_session  # type: ignore[attr-defined]
    except ImportError:
        typer.echo("Session execution module not available. Use 'testagent serve' to start the gateway first.")
        raise typer.Exit(1) from None

    import asyncio

    results = asyncio.run(run_session(skill_name=skill, env="ci"))
    tasks: list[dict[str, Any]] = results.get("tasks", [])

    for i, task in enumerate(tasks, 1):
        _output.print_task_result(i, len(tasks), task)

    passed = sum(1 for t in tasks if t.get("status") == "passed")
    failed = sum(1 for t in tasks if t.get("status") == "failed")
    duration = results.get("duration", "-")
    _output.print_summary(passed, failed, duration)

    if exit_code and failed > 0:
        raise typer.Exit(1)


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
