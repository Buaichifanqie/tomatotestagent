"""Draw action markers on screenshots using PIL.

Markers:
- tap: fluorescent yellow circle + crosshair (red if failed)
- swipe: fluorescent yellow arrow line (red if failed)
- input: blue dashed rectangle around input field
- assert: blue dashed rectangle around target area
- exec/wait/launch: no marker
"""
from __future__ import annotations

import base64
import math
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageDraw
except ImportError:
    Image = None  # type: ignore

from testagent.common.logging import get_logger

_logger = get_logger(__name__)

# ── Colors ────────────────────────────────────────────────────────────
_GREEN = (0, 200, 60)          # green for successful actions
_RED = (255, 40, 40)           # red for failures
_BLUE = (60, 140, 255)         # blue for input/assert
_BLACK = (0, 0, 0)             # black outline


def draw_marker_on_screenshot(
    b64_data: str,
    step_info: dict[str, Any] | None,
    success: bool = True,
) -> bytes | None:
    """Draw action marker on a screenshot and return PNG bytes.

    Args:
        b64_data: base64-encoded screenshot image.
        step_info: dict with keys 'action', 'x', 'y', 'start_x', 'start_y',
                   'end_x', 'end_y'. May be None for non-action screenshots.
        success: whether the action succeeded (affects color).

    Returns:
        PNG bytes with marker drawn, or None if PIL unavailable or error.
    """
    if Image is None or not step_info:
        return None

    action = step_info.get("action", "")
    if action in ("wait", "launch", "exec", "screenshot", ""):
        return None  # no marker for these actions

    try:
        import io
        img = Image.open(io.BytesIO(base64.b64decode(b64_data)))
        draw = ImageDraw.Draw(img)
        w, h = img.size

        # Scale factors based on image width (half of previous size)
        r = max(int(w * 0.02), 4)       # circle radius
        lw = max(int(w * 0.004), 1)     # line width

        if action == "tap":
            _draw_tap_marker(draw, step_info, w, h, r, lw, success)
        elif action == "swipe":
            _draw_swipe_marker(draw, step_info, w, h, r, lw, success)
        elif action in ("type", "input"):
            _draw_input_marker(draw, step_info, w, h, lw)
        elif action == "assert":
            _draw_assert_marker(draw, step_info, w, h, lw, success)

        # Encode back to PNG bytes
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    except Exception as e:
        _logger.debug("screenshot_marker: draw failed: %s", e)
        return None


def _draw_tap_marker(
    draw: Any, info: dict, w: int, h: int, r: int, lw: int, success: bool
) -> None:
    """Draw a circle + crosshair at tap position."""
    x = info.get("x")
    y = info.get("y")
    if x is None or y is None:
        return

    color = _GREEN if success else _RED
    outline = _BLACK

    # Outer circle with black outline
    draw.ellipse(
        [x - r, y - r, x + r, y + r],
        outline=outline, width=lw + 2,
    )
    draw.ellipse(
        [x - r, y - r, x + r, y + r],
        outline=color, width=lw,
    )
    # Crosshair
    arm = int(r * 1.5)
    draw.line([(x - arm, y), (x + arm, y)], fill=outline, width=lw + 1)
    draw.line([(x, y - arm), (x, y + arm)], fill=outline, width=lw + 1)
    draw.line([(x - arm, y), (x + arm, y)], fill=color, width=lw)
    draw.line([(x, y - arm), (x, y + arm)], fill=color, width=lw)

    # Failure X mark
    if not success:
        xarm = int(r * 1.2)
        draw.line([(x - xarm, y - xarm), (x + xarm, y + xarm)], fill=_RED, width=lw + 1)
        draw.line([(x - xarm, y + xarm), (x + xarm, y - xarm)], fill=_RED, width=lw + 1)


def _draw_swipe_marker(
    draw: Any, info: dict, w: int, h: int, r: int, lw: int, success: bool
) -> None:
    """Draw an arrow from start to end position."""
    sx = info.get("start_x")
    sy = info.get("start_y")
    ex = info.get("end_x")
    ey = info.get("end_y")
    if sx is None or sy is None or ex is None or ey is None:
        return

    color = _GREEN if success else _RED

    # Line with black outline
    draw.line([(sx, sy), (ex, ey)], fill=_BLACK, width=lw + 3)
    draw.line([(sx, sy), (ex, ey)], fill=color, width=lw + 1)

    # Start dot
    dr = max(r // 2, 4)
    draw.ellipse([sx - dr, sy - dr, sx + dr, sy + dr], fill=color, outline=_BLACK, width=2)

    # Arrowhead
    angle = math.atan2(ey - sy, ex - sx)
    arrow_len = int(r * 2.0)
    for da in (0.4, -0.4):
        ax = int(ex - arrow_len * math.cos(angle + da))
        ay = int(ey - arrow_len * math.sin(angle + da))
        draw.line([(ex, ey), (ax, ay)], fill=_BLACK, width=lw + 2)
        draw.line([(ex, ey), (ax, ay)], fill=color, width=lw)


def _draw_input_marker(
    draw: Any, info: dict, w: int, h: int, lw: int
) -> None:
    """Draw a blue dashed rectangle around input area."""
    x = info.get("x")
    y = info.get("y")
    if x is None or y is None:
        return

    # Estimate input field size (wider than tall)
    bw = int(w * 0.35)
    bh = int(h * 0.04)
    x1, y1 = x - bw // 2, y - bh // 2
    x2, y2 = x + bw // 2, y + bh // 2

    _draw_dashed_rect(draw, x1, y1, x2, y2, _BLUE, lw)


def _draw_assert_marker(
    draw: Any, info: dict, w: int, h: int, lw: int, success: bool
) -> None:
    """Draw a dashed rectangle for assert target area."""
    x = info.get("x")
    y = info.get("y")
    if x is None or y is None:
        return

    color = _BLUE if success else _RED
    bw = int(w * 0.3)
    bh = int(h * 0.05)
    x1, y1 = x - bw // 2, y - bh // 2
    x2, y2 = x + bw // 2, y + bh // 2

    _draw_dashed_rect(draw, x1, y1, x2, y2, color, lw)


def _draw_dashed_rect(
    draw: Any, x1: int, y1: int, x2: int, y2: int, color: tuple, lw: int
) -> None:
    """Draw a dashed rectangle."""
    dash = max(lw * 3, 8)
    gap = max(lw * 2, 4)
    # Top
    _draw_dashed_line(draw, x1, y1, x2, y1, color, lw, dash, gap)
    # Bottom
    _draw_dashed_line(draw, x1, y2, x2, y2, color, lw, dash, gap)
    # Left
    _draw_dashed_line(draw, x1, y1, x1, y2, color, lw, dash, gap)
    # Right
    _draw_dashed_line(draw, x2, y1, x2, y2, color, lw, dash, gap)


def _draw_dashed_line(
    draw: Any, x1: int, y1: int, x2: int, y2: int,
    color: tuple, lw: int, dash: int, gap: int,
) -> None:
    """Draw a dashed line from (x1,y1) to (x2,y2)."""
    dx = x2 - x1
    dy = y2 - y1
    length = math.sqrt(dx * dx + dy * dy)
    if length == 0:
        return
    ux, uy = dx / length, dy / length
    pos = 0.0
    while pos < length:
        sx = int(x1 + ux * pos)
        sy = int(y1 + uy * pos)
        end = min(pos + dash, length)
        ex = int(x1 + ux * end)
        ey = int(y1 + uy * end)
        draw.line([(sx, sy), (ex, ey)], fill=color, width=lw)
        pos += dash + gap
