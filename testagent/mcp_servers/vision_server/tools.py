from __future__ import annotations

import re
from typing import Any

from testagent.mcp_servers.vision_server.volcano_client import VolcanoVisionClient

_FIND_ELEMENT_PROMPT_TEMPLATE = """请在截图中找到以下目标：{target}

请分析：
1. 目标是否在当前屏幕中可见？
2. 如果可见，返回元素在截图中的百分比坐标（中心点和边界框）
3. 如果不可见，当前屏幕主要有什么内容？建议向哪个方向滑动来寻找目标？

请按以下格式回复：
- found: true/false
- 如果找到，提供 center 百分比坐标 (pct_x%, pct_y%) 和 bounds [pct_x1%, pct_y1%, pct_x2%, pct_y2%]
- 如果没找到，提供 suggestion 滑动方向
- 简要描述你看到的内容"""

_DESCRIBE_SCREEN_PROMPT = """请详细描述当前手机屏幕的内容，包括：
1. 屏幕上所有可交互的元素（应用图标、按钮、输入框、菜单等）
2. 每个元素的大致位置和功能描述（使用百分比坐标标注位置）
3. 屏幕的整体布局结构
4. 当前屏幕上用户可以执行哪些操作

请以结构化的方式列出所有元素。对于每个元素，提供百分比坐标 (pct_x%, pct_y%)。"""


def _parse_percentage_coordinates(
    text: str,
    device_w: int,
    device_h: int,
    image_w: int = 0,
    image_h: int = 0,
) -> dict[str, Any]:
    """Parse image-relative percentage coordinates and convert to device pixels.

    The vision model returns percentages relative to the screenshot image.
    This function converts them to device-pixel coordinates using the actual
    image dimensions (not device dimensions) for accurate mapping.

    Handles formats:
    - Center point: (45%, 50%) or (45 %, 50 %)
    - Bounding box: [30%, 40%, 60%, 70%]
    """
    result: dict[str, Any] = {}

    # Use image dimensions for conversion (model returns image-relative %)
    # Fall back to device dimensions if image dimensions unavailable
    ref_w = image_w if image_w > 0 else device_w
    ref_h = image_h if image_h > 0 else device_h

    # Center point: (pct_x%, pct_y%)
    center_match = re.search(
        r"[（(]\s*(\d+(?:\.\d+)?)\s*%\s*[，,]\s*(\d+(?:\.\d+)?)\s*%\s*[）)]", text
    )
    if center_match:
        img_pct_x = float(center_match.group(1))
        img_pct_y = float(center_match.group(2))
        # Convert image-relative % to device pixels
        # X: image width ≈ device width, so pct is the same
        # Y: image may be taller than device screen, need scaling
        device_x = round(device_w * img_pct_x / 100)
        device_y = round(device_h * (img_pct_y * ref_h / device_h) / 100)
        result["center"] = {"x": device_x, "y": device_y}
        result["center_pct"] = {"x": img_pct_x, "y": img_pct_y}

    # Bounding box: [pct_x1%, pct_y1%, pct_x2%, pct_y2%]
    bounds_match = re.search(
        r"\[\s*(\d+(?:\.\d+)?)\s*%\s*[，,]\s*(\d+(?:\.\d+)?)\s*%\s*[，,]\s*(\d+(?:\.\d+)?)\s*%\s*[，,]\s*(\d+(?:\.\d+)?)\s*%\s*\]",
        text,
    )
    if bounds_match:
        img_pct_x1 = float(bounds_match.group(1))
        img_pct_y1 = float(bounds_match.group(2))
        img_pct_x2 = float(bounds_match.group(3))
        img_pct_y2 = float(bounds_match.group(4))
        result["bounds"] = {
            "x1": round(device_w * img_pct_x1 / 100),
            "y1": round(device_h * (img_pct_y1 * ref_h / device_h) / 100),
            "x2": round(device_w * img_pct_x2 / 100),
            "y2": round(device_h * (img_pct_y2 * ref_h / device_h) / 100),
        }
        result["bounds_pct"] = {
            "x1": img_pct_x1, "y1": img_pct_y1,
            "x2": img_pct_x2, "y2": img_pct_y2,
        }

    return result


def _parse_found_status(text: str) -> bool:
    """Check if the model says the target is found."""
    lower = text.lower()
    if re.search(r"found[:\s]*true", lower):
        return True
    if re.search(r"found[:\s]*false", lower):
        return False
    # Has percentage coordinates → found
    if re.search(r"\d+(?:\.\d+)?\s*%", text):
        return True
    return False


def _parse_suggestion(text: str) -> str | None:
    """Parse navigation suggestion from response.

    Returns:
        Swipe direction string (swipe_up/down/left/right, scroll_up/down),
        or the full suggestion text if it contains a "tap to reveal" pattern,
        or None if no suggestion found.
    """
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

    # "tap to reveal" patterns: 点击...呼出/显示/浮现/唤起
    if re.search(r"点击.{2,15}?(?:呼出|显示|浮现|唤起|打开)", text):
        return text

    return None


async def vision_find_element(
    image: str | None = None,
    screenshot_id: str | None = None,
    target: str = "",
    context: str | None = None,
    vision_client: VolcanoVisionClient | None = None,
    device_width: int | None = None,
    device_height: int | None = None,
) -> dict[str, Any]:
    """Find a UI element on screen by visual analysis.

    Provide either image (direct base64) or screenshot_id (reference from app_screenshot).

    Args:
        image: Direct base64-encoded screenshot (legacy, prefer screenshot_id)
        screenshot_id: Reference key from app_screenshot result
        target: Natural language description of the target element
        context: Optional context from previous screen analysis
        vision_client: VolcanoVisionClient instance
        device_width: Real device screen width in pixels
        device_height: Real device screen height in pixels

    Returns:
        Dict with found, center, bounds, suggestion, description keys
    """
    actual_image = await _resolve_image(image=image, screenshot_id=screenshot_id)
    if isinstance(actual_image, dict):
        return actual_image  # error dict

    prompt = _FIND_ELEMENT_PROMPT_TEMPLATE.format(target=target)
    if context:
        prompt = f"之前的屏幕分析：{context}\n\n{prompt}"

    if vision_client is None:
        return {"error": "Vision client not initialized", "found": False}

    dw = device_width or 1080
    dh = device_height or 2400
    result = await vision_client.analyze(actual_image, prompt, device_width=dw, device_height=dh)
    if "error" in result:
        return {"error": result["error"], "found": False}

    content = result.get("content", "")

    # Extract actual image dimensions from vision result for coordinate conversion
    img_w = result.get("image_width", 0)
    img_h = result.get("image_height", 0)
    coords = _parse_percentage_coordinates(content, dw, dh, image_w=img_w, image_h=img_h)
    found = _parse_found_status(content)
    suggestion = _parse_suggestion(content)

    return {
        "found": found or bool(coords.get("center")),
        "center": coords.get("center"),
        "center_pct": coords.get("center_pct"),
        "bounds": coords.get("bounds"),
        "bounds_pct": coords.get("bounds_pct"),
        "suggestion": suggestion,
        "description": content.strip(),
    }


async def _resolve_image(
    image: str | None = None,
    screenshot_id: str | None = None,
) -> str | dict[str, Any]:
    """Resolve the actual base64 image data from either direct value or cache reference."""
    if image:
        return image

    if screenshot_id:
        from testagent.mcp_servers.shared_cache import get_screenshot

        data = get_screenshot(screenshot_id)
        if data is None:
            return {
                "error": f"Screenshot '{screenshot_id}' not found in cache (expired or invalid)",
                "found": False,
            }
        return data

    return {"error": "Either 'image' or 'screenshot_id' must be provided", "found": False}


async def vision_describe_screen(
    image: str | None = None,
    screenshot_id: str | None = None,
    vision_client: VolcanoVisionClient | None = None,
    device_width: int | None = None,
    device_height: int | None = None,
) -> dict[str, Any]:
    """Describe the current screen content visually.

    Provide either image (direct base64) or screenshot_id (reference from app_screenshot).

    Args:
        image: Direct base64-encoded screenshot (legacy, prefer screenshot_id)
        screenshot_id: Reference key from app_screenshot result
        vision_client: VolcanoVisionClient instance
        device_width: Real device screen width in pixels
        device_height: Real device screen height in pixels

    Returns:
        Dict with description, layout keys
    """
    actual_image = await _resolve_image(image=image, screenshot_id=screenshot_id)
    if isinstance(actual_image, dict):
        return actual_image  # error dict

    if vision_client is None:
        return {"error": "Vision client not initialized"}

    dw = device_width or 1080
    dh = device_height or 2400
    result = await vision_client.analyze(actual_image, _DESCRIBE_SCREEN_PROMPT, device_width=dw, device_height=dh)
    if "error" in result:
        return {"error": result["error"]}

    content = result.get("content", "")

    return {
        "description": content.strip(),
        "layout": content.strip()[:500],
    }
