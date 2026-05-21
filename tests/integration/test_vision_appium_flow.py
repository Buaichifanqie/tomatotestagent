from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.mcp_servers.vision_server.tools import (
    vision_describe_screen,
    vision_find_element,
)


@pytest.mark.asyncio
async def test_vision_find_then_tap_flow() -> None:
    """Simulate the full flow: screenshot -> vision find -> tap coordinates."""

    # Step 1: Take screenshot (mocked)
    screenshot_base64 = "mocked_base64_screenshot_data"

    # Step 2: Vision analysis finds the element
    mock_glm = AsyncMock()
    mock_glm.analyze.return_value = {
        "content": "found: true，找到美团 app 图标，中心坐标 (540, 1200)",
        "finish_reason": "stop",
    }

    vision_result = await vision_find_element(screenshot_base64, "美团 app", glm_client=mock_glm)
    assert vision_result["found"] is True
    assert vision_result["center"] == {"x": 540, "y": 1200}

    # Step 3: Use coordinates to tap via Appium
    center = vision_result["center"]
    tap_x, tap_y = center["x"], center["y"]

    # Verify coordinates are valid integers for tapping
    assert isinstance(tap_x, int)
    assert isinstance(tap_y, int)
    assert tap_x > 0
    assert tap_y > 0


@pytest.mark.asyncio
async def test_vision_smart_navigation_flow() -> None:
    """Simulate the smart navigation flow: not found -> swipe -> found."""

    screenshot_b64 = "mocked_screenshot"
    mock_glm = AsyncMock()

    # First analysis: not found, suggest swipe left
    mock_glm.analyze.return_value = {
        "content": "found: false，目标不在当前屏幕，当前屏幕显示的是主桌面第一页，有微信、支付宝等。建议向左滑动查看第二页。",
        "finish_reason": "stop",
    }

    result1 = await vision_find_element(screenshot_b64, "快手 app", glm_client=mock_glm)
    assert result1["found"] is False
    assert result1["suggestion"] == "swipe_left"

    # After swiping left, take another screenshot and re-analyze
    mock_glm.analyze.return_value = {
        "content": "found: true，找到快手 app 图标，位于屏幕第二行第三列，中心坐标 (800, 600)",
        "finish_reason": "stop",
    }

    result2 = await vision_find_element(screenshot_b64, "快手 app", glm_client=mock_glm)
    assert result2["found"] is True
    assert result2["center"] == {"x": 800, "y": 600}


@pytest.mark.asyncio
async def test_vision_describe_then_interact_flow() -> None:
    """Simulate: describe screen -> find search box -> type text."""

    screenshot_b64 = "mocked_screenshot"
    mock_glm = AsyncMock()

    # Step 1: Describe the screen
    mock_glm.analyze.return_value = {
        "content": "当前屏幕是美团 app 首页，顶部有搜索框，中间有各种分类图标（外卖、美食、酒店等），底部有导航栏。",
        "finish_reason": "stop",
    }

    describe_result = await vision_describe_screen(screenshot_b64, glm_client=mock_glm)
    assert "搜索框" in describe_result["description"]
    assert "error" not in describe_result

    # Step 2: Find the search box
    mock_glm.analyze.return_value = {
        "content": "found: true，搜索框位于屏幕顶部，中心坐标 (540, 150)，边界框 [100, 120, 980, 180]",
        "finish_reason": "stop",
    }

    find_result = await vision_find_element(screenshot_b64, "搜索框", glm_client=mock_glm)
    assert find_result["found"] is True
    assert find_result["center"] == {"x": 540, "y": 150}
    assert find_result["bounds"] == {"x1": 100, "y1": 120, "x2": 980, "y2": 180}
