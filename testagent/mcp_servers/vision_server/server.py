from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from testagent.config.settings import TestAgentSettings

from testagent.mcp_servers.base import BaseMCPServer
from testagent.mcp_servers.vision_server.glm_client import GLMClient
from testagent.mcp_servers.vision_server.tools import (
    vision_describe_screen,
    vision_find_element,
)


class VisionMCPServer(BaseMCPServer):
    server_name = "vision_server"

    def __init__(
        self,
        api_key: str = "",
        api_url: str = "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        model: str = "glm-4.6v-flash",
        timeout: int = 30,
        max_retries: int = 3,
    ) -> None:
        self._glm_client = GLMClient(
            api_key=api_key,
            api_url=api_url,
            model=model,
            timeout=timeout,
            max_retries=max_retries,
        )

    _tools_spec: ClassVar[list[dict[str, object]]] = [
        {
            "name": "vision_find_element",
            "description": "通过视觉分析在截图中查找目标 UI 元素，返回坐标和导航建议。先截取屏幕截图，再调用此工具进行分析。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "base64 编码的 PNG 截图数据",
                    },
                    "target": {
                        "type": "string",
                        "description": "要查找的目标的自然语言描述，如'美团 app 图标'、'搜索框'",
                    },
                    "context": {
                        "type": "string",
                        "description": "可选，之前的屏幕分析上下文，用于辅助导航决策",
                    },
                },
                "required": ["image", "target"],
            },
        },
        {
            "name": "vision_describe_screen",
            "description": "通过视觉分析描述当前屏幕的内容和布局，返回可交互元素列表和布局信息",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "image": {
                        "type": "string",
                        "description": "base64 编码的 PNG 截图数据",
                    },
                },
                "required": ["image"],
            },
        },
    ]

    _tool_registry: ClassVar[dict[str, Any]] = {
        "vision_find_element": vision_find_element,
        "vision_describe_screen": vision_describe_screen,
    }

    async def list_tools(self) -> list[dict[str, object]]:
        return self._tools_spec

    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
        tool = self._tool_registry.get(tool_name)
        if tool is None:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        try:
            from inspect import iscoroutinefunction

            injected = {**arguments, "glm_client": self._glm_client}
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
                "uri": "vision://status",
                "name": "Vision Server Status",
                "mimeType": "text/plain",
                "description": "Vision MCP Server 运行状态",
            },
        ]

    async def health_check(self) -> bool:
        return self._glm_client.is_configured

    @classmethod
    def from_settings(
        cls, settings: TestAgentSettings | None = None
    ) -> VisionMCPServer:
        """Create VisionMCPServer from TestAgentSettings."""
        if settings is None:
            from testagent.config.settings import get_settings

            settings = get_settings()
        return cls(
            api_key=settings.vision_api_key.get_secret_value(),
            api_url=settings.vision_api_url,
            model=settings.vision_model,
            timeout=settings.vision_timeout,
            max_retries=settings.vision_max_retries,
        )
