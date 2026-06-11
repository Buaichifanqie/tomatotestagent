from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from testagent.db_ops.models import SqlOperation, SqlOperationType

console = Console()


class ConfirmUI:
    """Rich layered confirmation display for database write operations.

    Shows operation details with layered priority:
    1. SQL preview (highest priority — always visible)
    2. Operation type badge + description
    3. Parameters
    4. Safety warnings
    """

    # Color mapping for operation types
    _TYPE_COLORS: dict[str, str] = {
        "SELECT": "green",
        "INSERT": "yellow",
        "UPDATE": "red",
    }

    def confirm_operation(self, operation: SqlOperation) -> bool:
        """Display operation details and ask for user confirmation.

        Returns True if user confirms, False if rejected.
        """
        self._render_header()
        self._render_sql(operation)
        self._render_type_badge(operation)
        self._render_description(operation)
        self._render_params(operation)
        self._render_warnings(operation)

        console.print()
        response = input("  确认执行此操作？ [y/N]: ").strip().lower()
        return response in ("y", "yes")

    def show_result(
        self,
        success: bool,
        operation: SqlOperation,
        rows_affected: int = 0,
        data: list[dict[str, Any]] | None = None,
        error_message: str = "",
        duration_ms: int = 0,
    ) -> None:
        """Display execution result."""
        if success:
            status = Text("  SUCCESS", style="bold green")
            if operation.type == SqlOperationType.SELECT and data:
                self._render_data_table(data)
            console.print(f"{status}  |  {rows_affected} rows  |  {duration_ms}ms")
        else:
            status = Text("  FAILED", style="bold red")
            console.print(f"{status}  |  {error_message}  |  {duration_ms}ms")

    def show_cleanup_plan(self, operations: list[SqlOperation]) -> None:
        """Display cleanup operations that will be executed."""
        if not operations:
            return

        console.print()
        console.print(Panel(
            "[bold]清理计划[/bold]",
            title="Cleanup Plan",
            border_style="yellow",
        ))

        table = Table(show_header=True, header_style="bold")
        table.add_column("#", width=3)
        table.add_column("SQL", min_width=40)
        table.add_column("说明")

        for i, op in enumerate(operations, 1):
            table.add_row(str(i), op.sql, op.description)

        console.print(table)

    # -- Private rendering methods --------------------------------------------

    def _render_header(self) -> None:
        console.print()
        console.print(Panel(
            "[bold cyan]数据库操作确认[/bold cyan]",
            subtitle="AI Database Operation Confirmation",
            border_style="cyan",
        ))

    def _render_sql(self, operation: SqlOperation) -> None:
        console.print()
        console.print("[bold]SQL 语句:[/bold]")
        syntax = Syntax(
            operation.sql,
            "sql",
            theme="monokai",
            line_numbers=False,
            word_wrap=True,
        )
        console.print(Panel(syntax, border_style="blue"))

    def _render_type_badge(self, operation: SqlOperation) -> None:
        color = self._TYPE_COLORS.get(operation.type.value, "white")
        badge = Text(f"  {operation.type.value} ", style=f"bold {color} on black")
        console.print(f"  操作类型: {badge}")

    def _render_description(self, operation: SqlOperation) -> None:
        if operation.description:
            console.print(f"  说明: {operation.description}")

    def _render_params(self, operation: SqlOperation) -> None:
        if operation.params:
            console.print("  参数:")
            for key, value in operation.params.items():
                console.print(f"    :{key} = {value!r}")

    def _render_warnings(self, operation: SqlOperation) -> None:
        if operation.type == SqlOperationType.INSERT:
            console.print("  [yellow]  注意: 将向数据库插入新记录[/yellow]")
        elif operation.type == SqlOperationType.UPDATE:
            console.print("  [red]  警告: 将修改数据库中的现有记录[/red]")

    def _render_data_table(self, data: list[dict[str, Any]]) -> None:
        if not data:
            return

        table = Table(
            title="查询结果",
            show_header=True,
            header_style="bold cyan",
            show_lines=True,
        )

        columns = list(data[0].keys())
        for col in columns:
            table.add_column(col, overflow="ellipsis", max_width=30)

        for row in data:
            table.add_row(*[str(v) for v in row.values()])

        console.print(table)
