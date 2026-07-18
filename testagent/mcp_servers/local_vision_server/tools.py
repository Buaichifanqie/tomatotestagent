"""Local Vision MCP 工具实现。

参考 ``vision_server/tools.py`` 的模式。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from testagent.mcp_servers.shared_cache import get_screenshot
from testagent.vision_local.engine import LocalVisionEngine

logger = logging.getLogger(__name__)


async def local_vision_get_page_structure(
    screenshot_id: str = "",
    dom_xml: str = "",
    source: str = "auto",
    engine: LocalVisionEngine | None = None,
) -> dict[str, Any]:
    """使用本地 YOLOv8 + OCR 分析当前屏幕，返回结构化页面元素 JSON。

    Args:
        screenshot_id: app_screenshot 返回的截图引用 ID。
        dom_xml: uiautomator2 的 XML 源码。
        source: "auto"（DOM优先）, "dom", "visual"。
        engine: LocalVisionEngine 实例。

    Returns:
        结构化页面数据 dict。
    """
    if engine is None:
        return {"error": "LocalVisionEngine not initialized"}

    b64 = ""
    if screenshot_id:
        b64 = get_screenshot(screenshot_id) or ""

    result = await engine.get_page_structure(
        screenshot_base64=b64,
        dom_xml=dom_xml,
        source_hint=source,
    )
    return result


async def local_vision_find_element(
    target: str = "",
    page_structure: dict | None = None,
    engine: LocalVisionEngine | None = None,
    llm_provider: Any = None,
) -> dict[str, Any]:
    """在结构化页面数据中查找目标 UI 元素，返回坐标。

    Args:
        target: 目标元素描述。
        page_structure: ``local_vision_get_page_structure`` 返回的数据。
        engine: LocalVisionEngine 实例。
        llm_provider: LLM provider。

    Returns:
        {"x": int, "y": int, "element_id": str} 或 {"error": str, "found": false}。
    """
    if engine is None:
        return {"error": "LocalVisionEngine not initialized", "found": False}
    if not target:
        return {"error": "target is required", "found": False}

    ps = page_structure or {}
    coords = await engine.find_element_by_llm(
        target=target,
        page_structure=ps,
        llm_provider=llm_provider,
    )
    if coords:
        return {**coords, "found": True}
    return {"found": False, "error": f"Element '{target}' not found"}
