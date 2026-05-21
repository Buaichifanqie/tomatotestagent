from __future__ import annotations

import re
from typing import Any

from testagent.mcp_servers.vision_server.glm_client import GLMClient

_FIND_ELEMENT_PROMPT_TEMPLATE = """请在截图中找到以下目标：{target}

请分析：
1. 目标是否在当前屏幕中可见？
2. 如果可见，返回元素的坐标（中心点和边界框）
3. 如果不可见，当前屏幕主要有什么内容？建议向哪个方向滑动来寻找目标？

请按以下格式回复：
- found: true/false
- 如果找到，提供 center 坐标 (x, y) 和 bounds [x1, y1, x2, y2]
- 如果没找到，提供 suggestion 滑动方向
- 简要描述你看到的内容"""

_DESCRIBE_SCREEN_PROMPT = """请详细描述当前手机屏幕的内容，包括：
1. 屏幕上所有可交互的元素（应用图标、按钮、输入框、菜单等）
2. 每个元素的大致位置和功能描述
3. 屏幕的整体布局结构
4. 当前屏幕上用户可以执行哪些操作

请以结构化的方式列出所有元素。"""


def _parse_coordinates(text: str) -> dict[str, Any]:
    """Parse coordinate information from GLM response text.

    Handles multiple formats:
    - Center point: (x, y)
    - Bounding box: [x1, y1, x2, y2]
    """
    result: dict[str, Any] = {}

    center_match = re.search(r"[（(]\s*(\d+)\s*[，,]\s*(\d+)\s*[）)]", text)
    if center_match:
        result["center"] = {
            "x": int(center_match.group(1)),
            "y": int(center_match.group(2)),
        }

    bounds_match = re.search(
        r"\[\s*(\d+)\s*[，,]\s*(\d+)\s*[，,]\s*(\d+)\s*[，,]\s*(\d+)\s*\]", text
    )
    if bounds_match:
        result["bounds"] = {
            "x1": int(bounds_match.group(1)),
            "y1": int(bounds_match.group(2)),
            "x2": int(bounds_match.group(3)),
            "y2": int(bounds_match.group(4)),
        }

    return result


def _parse_found_status(text: str) -> bool:
    """Check if the model says the target is found."""
    lower = text.lower()
    if re.search(r"found[:\s]*true", lower):
        return True
    if re.search(r"found[:\s]*false", lower):
        return False
    if re.search(r"[（(]\s*\d+\s*[，,]\s*\d+\s*[）)]", text):
        return True
    return False


def _parse_suggestion(text: str) -> str | None:
    """Parse navigation suggestion from response."""
    lower = text.lower()
    swipe_patterns = [
        (r"swipe_left|向左滑|左滑|向左划", "swipe_left"),
        (r"swipe_right|向右滑|右滑|向右划", "swipe_right"),
        (r"swipe_up|向上滑|上滑|向上划", "swipe_up"),
        (r"swipe_down|向下滑|下滑|向下划", "swipe_down"),
        (r"scroll_down|向下滚动", "scroll_down"),
        (r"scroll_up|向上滚动", "scroll_up"),
    ]
    for pattern, suggestion in swipe_patterns:
        if re.search(pattern, lower):
            return suggestion
    return None


async def vision_find_element(
    image: str,
    target: str,
    context: str | None = None,
    glm_client: GLMClient | None = None,
) -> dict[str, Any]:
    """Find a UI element on screen by visual analysis.

    Args:
        image: Base64-encoded screenshot
        target: Natural language description of the target element
        context: Optional context from previous screen analysis
        glm_client: GLMClient instance

    Returns:
        Dict with found, center, bounds, suggestion, description keys
    """
    prompt = _FIND_ELEMENT_PROMPT_TEMPLATE.format(target=target)
    if context:
        prompt = f"之前的屏幕分析：{context}\n\n{prompt}"

    if glm_client is None:
        return {"error": "GLM client not initialized", "found": False}

    result = await glm_client.analyze(image, prompt)
    if "error" in result:
        return {"error": result["error"], "found": False}

    content = result.get("content", "")

    coords = _parse_coordinates(content)
    found = _parse_found_status(content)
    suggestion = _parse_suggestion(content)

    return {
        "found": found or bool(coords.get("center")),
        "center": coords.get("center"),
        "bounds": coords.get("bounds"),
        "suggestion": suggestion,
        "description": content.strip(),
    }


async def vision_describe_screen(
    image: str,
    glm_client: GLMClient | None = None,
) -> dict[str, Any]:
    """Describe the current screen content visually.

    Args:
        image: Base64-encoded screenshot
        glm_client: GLMClient instance

    Returns:
        Dict with description, layout keys
    """
    if glm_client is None:
        return {"error": "GLM client not initialized"}

    result = await glm_client.analyze(image, _DESCRIBE_SCREEN_PROMPT)
    if "error" in result:
        return {"error": result["error"]}

    content = result.get("content", "")

    return {
        "description": content.strip(),
        "layout": content.strip()[:500],
    }
