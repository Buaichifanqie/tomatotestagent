from __future__ import annotations

import asyncio
import json
from pathlib import Path  # noqa: TC003
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from testagent.config.settings import get_settings

mcp_app = typer.Typer(name="mcp", help="Manage MCP server connections", no_args_is_help=True)
_console = Console()


@mcp_app.command("add")
def mcp_add(
    name: str = typer.Argument(help="MCP server name"),
    command: str = typer.Option("python", "--command", "-c", help="Command to start the server"),
    config: Path | None = typer.Option(None, "--config", help="Path to MCP config JSON file"),  # noqa: B008
) -> None:
    """Add a new MCP server configuration."""
    from testagent.config.settings import get_settings
    from testagent.gateway.mcp_registry import MCPRegistry
    from testagent.models.mcp_config import MCPConfig

    args: dict[str, object] = {}
    env_vars: dict[str, object] = {}

    if config and config.exists():
        raw = json.loads(config.read_text("utf-8"))
        args = raw.get("args", {})
        env_vars = raw.get("env", {})

    mcp_config = MCPConfig(server_name=name, command=command, args=args, env=env_vars)

    async def _register() -> None:
        settings = get_settings()
        registry = MCPRegistry(settings)
        await registry.register(mcp_config)

    asyncio.run(_register())
    typer.echo(f"MCP server '{name}' added (command: {command})")


@mcp_app.command("list")
def mcp_list() -> None:
    """List all configured MCP servers."""
    from testagent.gateway.mcp_registry import MCPRegistry

    settings = get_settings()
    registry = MCPRegistry(settings)

    async def _list() -> list[Any]:
        return await registry.list_servers()

    servers = asyncio.run(_list())

    if not servers:
        typer.echo("No MCP servers configured.")
        return

    table = Table(title="MCP Servers")
    table.add_column("Name", style="cyan")
    table.add_column("Status", style="bold")
    table.add_column("Command")
    table.add_column("Tools")

    for s in servers:
        status_style = "green" if s.status == "healthy" else "red"
        tools_count = str(len(s.tools))
        table.add_row(s.name, f"[{status_style}]{s.status}[/{status_style}]", s.command, tools_count)

    _console.print(table)


@mcp_app.command("health")
def mcp_health(
    name: str | None = typer.Argument(None, help="MCP server name to check (omit for all)"),
) -> None:
    """Check MCP server health status."""
    from testagent.gateway.mcp_registry import MCPRegistry

    settings = get_settings()
    registry = MCPRegistry(settings)

    async def _check_single(server_name: str) -> None:
        try:
            info = await registry.lookup(server_name)
            typer.echo(f"{server_name}: {info.status}")
        except Exception:
            typer.echo(f"{server_name}: not_found")

    async def _check_all() -> list[Any]:
        return await registry.list_servers()

    if name:
        asyncio.run(_check_single(name))
    else:
        servers = asyncio.run(_check_all())
        if not servers:
            typer.echo("No MCP servers configured.")
            return
        for s in servers:
            status_icon = "✓" if s.status == "healthy" else "✗"
            typer.echo(f"{s.name:<30} {status_icon} {s.status}")
