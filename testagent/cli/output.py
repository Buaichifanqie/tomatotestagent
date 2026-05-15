from __future__ import annotations

from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


class RichOutput:
    def __init__(self) -> None:
        self._console = Console()

    def print_header(self, skill: str, target: str, timeout: str) -> None:
        panel = Panel(
            Text.from_markup(
                f"[bold cyan]Skill[/bold cyan]: {skill}\n"
                f"[bold cyan]Target[/bold cyan]: {target}\n"
                f"[bold cyan]Timeout[/bold cyan]: {timeout}"
            ),
            title="[bold]TestAgent Run[/bold]",
            border_style="cyan",
        )
        self._console.print(panel)

    def print_task_result(self, index: int, total: int, task: dict[str, Any]) -> None:
        status = task.get("status", "unknown")
        name = task.get("name", f"Task #{index}")
        duration = task.get("duration", "-")

        status_style = {
            "passed": "green",
            "failed": "red",
            "flaky": "yellow",
            "skipped": "dim",
            "running": "blue",
            "queued": "white",
        }.get(status, "white")

        self._console.print(
            f"[{status_style}]{'✓' if status == 'passed' else '✗' if status == 'failed' else '~'} "
            f"[bold]{name}[/bold]  "
            f"[{status_style}]({status})[/{status_style}]  "
            f"[dim]{duration}[/dim]"
        )

    def print_summary(self, passed: int, failed: int, duration: str) -> None:
        total = passed + failed
        table = Table(box=box.ROUNDED, title="[bold]Summary[/bold]")
        table.add_column("Result", style="bold")
        table.add_column("Count", justify="right")
        table.add_row("[green]Passed[/green]", str(passed))
        if failed > 0:
            table.add_row("[red]Failed[/red]", str(failed))
        table.add_row("[bold]Total[/bold]", str(total))
        table.add_row("[cyan]Duration[/cyan]", duration)

        panel = Panel(table, border_style="red" if failed > 0 else "green")

        self._console.print(panel)

    def print_error(self, task_id: str, error: str) -> None:
        self._console.print(
            f"[red]✗ Task {task_id} failed:[/red]\n{error}",
            style="bold red",
        )
