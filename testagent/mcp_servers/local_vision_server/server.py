"""Local Vision MCP Server。

遵循 ``VisionMCPServer`` 的模式，继承 ``BaseMCPServer``。
"""
from __future__ import annotations

import json
from inspect import iscoroutinefunction
from typing import Any, ClassVar

from testagent.mcp_servers.base import BaseMCPServer
from testagent.mcp_servers.local_vision_server.tools import (
    local_vision_find_element,
    local_vision_get_page_structure,
)
from testagent.vision_local.engine import LocalVisionEngine
from testagent.vision_local.recognizer import PageElementRecognizer


class LocalVisionMCPServer(BaseMCPServer):
    """本地视觉 MCP 服务器：封装 YOLOv8+OCR+DOM 页面解析能力。"""

    server_name = "local_vision_server"

    def __init__(self, engine: LocalVisionEngine | None = None) -> None:
        self._engine = engine or self._create_default_engine()

    _tools_spec: ClassVar[list[dict[str, object]]] = [
        {
            "name": "local_vision_get_page_structure",
            "description": "使用本地 YOLOv8+OCR 分析当前屏幕截图，返回结构化页面元素 JSON（包含所有元素的类型、坐标、置信度、文字内容、颜色状态）。先调用 app_screenshot 获取截图 ID，再将 screenshot_id 传入此工具。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "screenshot_id": {
                        "type": "string",
                        "description": "app_screenshot 返回的截图引用 ID",
                    },
                    "dom_xml": {
                        "type": "string",
                        "description": "uiautomator2 的 XML 源码（可选，提供则使用 DOM 通道）",
                    },
                    "source": {
                        "type": "string",
                        "description": "解析策略: auto(DOM优先), dom(强制DOM), visual(强制视觉)",
                        "default": "auto",
                    },
                },
            },
        },
        {
            "name": "local_vision_find_element",
            "description": "在结构化页面数据中查找目标 UI 元素，返回像素坐标。需要先调用 local_vision_get_page_structure 获取页面数据",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "要查找的目标自然语言描述，如'搜索框'、'首页Tab'",
                    },
                    "page_structure": {
                        "type": "object",
                        "description": "local_vision_get_page_structure 返回的页面数据",
                    },
                },
                "required": ["target"],
            },
        },
    ]

    _tool_registry: ClassVar[dict[str, Any]] = {
        "local_vision_get_page_structure": local_vision_get_page_structure,
        "local_vision_find_element": local_vision_find_element,
    }

    async def list_tools(self) -> list[dict[str, object]]:
        return self._tools_spec

    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
        tool = self._tool_registry.get(tool_name)
        if tool is None:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        try:
            injected = {**arguments, "engine": self._engine}
            if iscoroutinefunction(tool):
                result = await tool(**injected)
            else:
                result = tool(**injected)
            if isinstance(result, (dict, list)):
                return json.dumps(result, ensure_ascii=False)
            return str(result)
        except Exception as e:
            return json.dumps({"error": str(e)}, ensure_ascii=False)

    async def list_resources(self) -> list[dict[str, object]]:
        return [
            {
                "uri": "local-vision://status",
                "name": "Local Vision Server Status",
                "mimeType": "text/plain",
                "description": "本地 YOLOv8+OCR 视觉识别服务状态",
            },
        ]

    async def health_check(self) -> bool:
        return self._engine is not None

    @classmethod
    def from_settings(
        cls, settings: Any = None
    ) -> LocalVisionMCPServer:
        """从应用配置创建 LocalVisionMCPServer。"""
        if settings is None:
            from testagent.config.settings import get_settings

            settings = get_settings()

        recognizer = PageElementRecognizer(
            model_path=settings.yolo_model_path or "",
            confidence_threshold=settings.yolo_confidence_threshold,
            iou_threshold=settings.yolo_iou_threshold,
            ocr_engine=settings.ocr_engine,
            ocr_confidence=settings.ocr_confidence_threshold,
            device=settings.yolo_device,
        )
        engine = LocalVisionEngine(
            recognizer=recognizer,
            use_dom=True,
        )
        return cls(engine=engine)

    @staticmethod
    def _create_default_engine() -> LocalVisionEngine:
        """创建默认引擎（settings 未配置时用空 recognizer）。"""
        return LocalVisionEngine(recognizer=None, use_dom=True)
