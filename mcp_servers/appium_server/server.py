from __future__ import annotations

import json
from typing import Any, ClassVar

import httpx

from testagent.mcp_servers.appium_server.tools import (
    app_assert_element,
    app_get_source,
    app_install,
    app_screenshot,
    app_swipe,
    app_tap,
    app_type,
)
from testagent.mcp_servers.base import BaseMCPServer


class AppiumMCPServer(BaseMCPServer):
    server_name = "appium_server"

    def __init__(self, appium_url: str = "http://localhost:4723") -> None:
        self._appium_url = appium_url.rstrip("/")

    _tools_spec: ClassVar[list[dict[str, object]]] = [
        {
            "name": "app_install",
            "description": "Install an app to the device, return {status_code, body}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "app_path": {"type": "string", "description": "Path or URL to the app package (.apk/.ipa)"},
                },
                "required": ["app_path"],
            },
        },
        {
            "name": "app_tap",
            "description": "Tap on an element, return {status_code, body} or {error}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "Element selector"},
                    "strategy": {
                        "type": "string",
                        "description": "Locator strategy: accessibility_id, uiautomator, or xpath",
                        "enum": ["accessibility_id", "uiautomator", "xpath"],
                    },
                },
                "required": ["selector"],
            },
        },
        {
            "name": "app_swipe",
            "description": "Perform a swipe gesture, return {status_code, body}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "start_x": {"type": "integer", "description": "Start X coordinate"},
                    "start_y": {"type": "integer", "description": "Start Y coordinate"},
                    "end_x": {"type": "integer", "description": "End X coordinate"},
                    "end_y": {"type": "integer", "description": "End Y coordinate"},
                    "duration": {"type": "integer", "description": "Swipe duration in ms, default 500"},
                },
                "required": ["start_x", "start_y", "end_x", "end_y"],
            },
        },
        {
            "name": "app_type",
            "description": "Type text into an element, return {status_code, body} or {error}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "Element selector"},
                    "text": {"type": "string", "description": "Text to type"},
                    "strategy": {
                        "type": "string",
                        "description": "Locator strategy: accessibility_id, uiautomator, or xpath",
                        "enum": ["accessibility_id", "uiautomator", "xpath"],
                    },
                },
                "required": ["selector", "text"],
            },
        },
        {
            "name": "app_assert_element",
            "description": "Assert element state (visible/text/attribute), return {passed, reason} or {error}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "Element selector"},
                    "assertion": {
                        "type": "string",
                        "description": "Assertion type: visible, text, or attribute",
                        "enum": ["visible", "text", "attribute"],
                    },
                    "expected": {
                        "type": "string",
                        "description": "Expected value (for text/attribute assertions)",
                    },
                    "strategy": {
                        "type": "string",
                        "description": "Locator strategy: accessibility_id, uiautomator, or xpath",
                        "enum": ["accessibility_id", "uiautomator", "xpath"],
                    },
                },
                "required": ["selector", "assertion"],
            },
        },
        {
            "name": "app_screenshot",
            "description": "Take a screenshot of the app, return {screenshot_base64, format}",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "app_get_source",
            "description": "Get the current page XML source, return {source, format}",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
    ]

    _tool_registry: ClassVar[dict[str, Any]] = {
        "app_install": app_install,
        "app_tap": app_tap,
        "app_swipe": app_swipe,
        "app_type": app_type,
        "app_assert_element": app_assert_element,
        "app_screenshot": app_screenshot,
        "app_get_source": app_get_source,
    }

    async def list_tools(self) -> list[dict[str, object]]:
        return self._tools_spec

    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
        tool = self._tool_registry.get(tool_name)
        if tool is None:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        try:
            from inspect import iscoroutinefunction

            injected = {**arguments, "appium_url": self._appium_url}
            if iscoroutinefunction(tool):
                result = await tool(**injected)
            else:
                result = tool(**injected)
            if isinstance(result, (dict, list)):
                return json.dumps(result)
            return str(result)
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def list_resources(self) -> list[dict[str, object]]:
        return [
            {
                "uri": "app://source",
                "name": "Current Page Source",
                "mimeType": "application/xml",
                "description": "XML source tree of the current app screen",
            },
            {
                "uri": "app://screenshot",
                "name": "Current Screenshot",
                "mimeType": "image/png",
                "description": "Base64-encoded PNG screenshot of the current app screen",
            },
        ]

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as client:
                response = await client.get(f"{self._appium_url}/status")
                return response.status_code == 200
        except Exception:
            return False
