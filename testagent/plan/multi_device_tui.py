"""Multi-device parallel execution TUI (terminal UI).

Uses ``rich`` to render one Panel per device + a summary bar below.
Every device gets its own accent colour (cyan / green / yellow) so
logs are visually distinct at a glance.

Usage::

    tui = MultiDeviceTUI(devices)
    tui.start()
    # ... from worker threads ...
    tui.update_log(udid, "Step 1 passed", "success")
    tui.update_log(udid, "Retrying (1/3)...", "retry")
    # ...
    tui.stop()
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from testagent.plan.device_manager import DeviceInfo


# Colour per device index
_DEVICE_COLORS = ["cyan", "green", "yellow"]
_LEVEL_STYLES = {
    "info": "",
    "success": "bold green",
    "error": "bold red",
    "retry": "bold yellow",
    "warn": "yellow",
}


@dataclass
class DeviceLog:
    lines: list[tuple[str, str]] = field(default_factory=list)  # (message, style)
    max_lines: int = 20


class MultiDeviceTUI:
    """Live TUI showing per-device execution panels."""

    def __init__(self, devices: list[DeviceInfo]) -> None:
        self.devices = devices
        self.console = Console()
        self._logs: dict[str, DeviceLog] = {d.udid: DeviceLog() for d in devices}
        self._summary: dict[str, str] = {}
        self._lock = threading.Lock()
        self._live: Optional[Live] = None

    def start(self) -> None:
        """Start the live display."""
        layout = self._build_layout()
        self._live = Live(layout, console=self.console, refresh_per_second=4, screen=True)
        self._live.__enter__()

    def stop(self) -> None:
        """Stop the live display."""
        if self._live:
            try:
                self._live.__exit__(None, None, None)
            except Exception:
                pass
            self._live = None
        self.console.print()

    def update_log(self, udid: str, message: str, level: str = "info") -> None:
        """Append a log line to device *udid*'s panel."""
        style = _LEVEL_STYLES.get(level, "")
        with self._lock:
            log = self._logs.get(udid)
            if log is None:
                return
            log.lines.append((message, style))
            if len(log.lines) > log.max_lines:
                log.lines.pop(0)
        self._refresh()

    def update_summary(self, udid: str, status: str) -> None:
        """Update device status in the summary bar."""
        with self._lock:
            self._summary[udid] = status
        self._refresh()

    def _refresh(self) -> None:
        if self._live:
            self._live.update(self._build_layout())

    def _build_layout(self) -> Layout:
        layout = Layout()
        layout.split_column(
            Layout(name="panels", ratio=4),
            Layout(name="summary", ratio=1),
        )

        panel_layout = Layout()
        if len(self.devices) == 1:
            panel_layout.split_row(Layout(name="d0"))
        elif len(self.devices) == 2:
            panel_layout.split_row(Layout(name="d0"), Layout(name="d1"))
        else:
            panel_layout.split_row(*[Layout(name=f"d{i}") for i in range(len(self.devices))])

        with self._lock:
            for i, d in enumerate(self.devices):
                color = _DEVICE_COLORS[i % len(_DEVICE_COLORS)]
                log = self._logs.get(d.udid, DeviceLog())
                styled_lines = []
                for line, style in log.lines:
                    styled_lines.append(Text(line, style=style) if style else Text(line))
                content = Text("\n").join(styled_lines) if styled_lines else Text("Waiting...", style="dim")
                panel = Panel(
                    content,
                    title=f"[bold {color}]{d.name}[/] ({d.udid})",
                    border_style=color,
                )
                panel_layout[f"d{i}"].update(panel)

        layout["panels"].update(panel_layout)

        with self._lock:
            summary_text = f"Devices: {len(self._summary)}/{len(self.devices)} active"
        layout["summary"].update(
            Panel(summary_text, title="Summary", border_style="blue")
        )

        return layout
