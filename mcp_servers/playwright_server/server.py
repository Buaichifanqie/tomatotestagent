from __future__ import annotations

import inspect
import json
from typing import Any, ClassVar

from testagent.mcp_servers.base import BaseMCPServer
from testagent.mcp_servers.playwright_server.tools import (
    browser_assert,
    browser_click,
    browser_get_console,
    browser_get_network,
    browser_navigate,
    browser_screenshot,
    browser_type,
)


class PlaywrightMCPServer(BaseMCPServer):
    server_name = "playwright_server"

    _tools_spec: ClassVar[list[dict[str, object]]] = [
        {
            "name": "browser_navigate",
            "description": "Navigate to a URL, return {url, title, status_code}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to navigate to"},
                    "wait_until": {
                        "type": "string",
                        "enum": ["load", "domcontentloaded", "networkidle"],
                        "description": "When to consider navigation succeeded, default 'load'",
                    },
                },
                "required": ["url"],
            },
        },
        {
            "name": "browser_click",
            "description": "Click an element on the page",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the element"},
                    "button": {
                        "type": "string",
                        "enum": ["left", "right", "middle"],
                        "description": "Mouse button to click, default 'left'",
                    },
                },
                "required": ["selector"],
            },
        },
        {
            "name": "browser_type",
            "description": "Type text into an input element",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the input element"},
                    "text": {"type": "string", "description": "Text to type"},
                    "clear": {"type": "boolean", "description": "Clear existing text before typing, default true"},
                },
                "required": ["selector", "text"],
            },
        },
        {
            "name": "browser_screenshot",
            "description": "Take a screenshot of the page or element, return base64-encoded PNG",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for a specific element (optional)"},
                    "full_page": {"type": "boolean", "description": "Capture full scrollable page, default false"},
                },
                "required": [],
            },
        },
        {
            "name": "browser_assert",
            "description": "Assert a condition on the page or element, return {assertion, passed, ...}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "selector": {"type": "string", "description": "CSS selector for the target element"},
                    "assertion": {
                        "type": "string",
                        "enum": [
                            "visible",
                            "hidden",
                            "enabled",
                            "disabled",
                            "exists",
                            "text",
                            "value",
                            "attribute",
                            "count",
                            "url",
                            "title",
                        ],
                        "description": "Type of assertion to perform",
                    },
                    "expected": {
                        "type": "string",
                        "description": "Expected value (required for text/value/attribute/count)",
                    },
                },
                "required": ["selector", "assertion"],
            },
        },
        {
            "name": "browser_get_console",
            "description": "Retrieve collected browser console messages",
            "inputSchema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "name": "browser_get_network",
            "description": "Retrieve collected network requests, optionally filtered by URL pattern",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "url_pattern": {"type": "string", "description": "Regex pattern to filter requests by URL"},
                },
                "required": [],
            },
        },
    ]

    _raw_tool_registry: ClassVar[dict[str, Any]] = {
        "browser_navigate": browser_navigate,
        "browser_click": browser_click,
        "browser_type": browser_type,
        "browser_screenshot": browser_screenshot,
        "browser_assert": browser_assert,
        "browser_get_console": browser_get_console,
        "browser_get_network": browser_get_network,
    }

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._console_messages: list[dict[str, object]] = []
        self._network_requests: list[dict[str, object]] = []

    async def _ensure_browser(self) -> None:
        if self._page is not None:
            return

        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        self._context = await self._browser.new_context()
        self._page = await self._context.new_page()

        self._page.on(
            "console",
            lambda msg: self._console_messages.append(
                {
                    "type": msg.type,
                    "text": msg.text,
                    "location": str(msg.location) if msg.location else "",
                    "timestamp": self._page.url if self._page else "",
                }
            ),
        )
        self._page.on(
            "request",
            lambda request: self._network_requests.append(
                {
                    "url": request.url,
                    "method": request.method,
                    "headers": dict(request.headers),
                    "timestamp": request.url,
                }
            ),
        )
        self._page.on(
            "requestfinished",
            lambda request: self._network_requests.append(
                {
                    "url": request.url,
                    "method": request.method,
                    "status_code": getattr(request.response(), "status", None) if request.response() else None,
                    "headers": dict(request.headers),
                    "duration_ms": None,
                }
            ),
        )

    async def _cleanup_browser(self) -> None:
        try:
            if self._context:
                await self._context.close()
        finally:
            pass
        try:
            if self._browser:
                await self._browser.close()
        finally:
            pass
        try:
            if self._playwright:
                await self._playwright.stop()
        finally:
            pass
        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None

    async def list_tools(self) -> list[dict[str, object]]:
        return self._tools_spec

    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
        tool = self._raw_tool_registry.get(tool_name)
        if tool is None:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        try:
            sig = inspect.signature(tool)
            extra_kwargs: dict[str, object] = {}
            if "page" in sig.parameters:
                extra_kwargs["page"] = self._page
            if "console_messages" in sig.parameters:
                extra_kwargs["console_messages"] = self._console_messages
            if "network_requests" in sig.parameters:
                extra_kwargs["network_requests"] = self._network_requests
            result = await tool(**arguments, **extra_kwargs)
            if isinstance(result, (dict, list)):
                return json.dumps(result)
            return str(result)
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def list_resources(self) -> list[dict[str, object]]:
        return [
            {
                "uri": "page://dom",
                "name": "DOM",
                "mimeType": "text/html",
                "description": "Current page DOM content",
            },
            {
                "uri": "page://console",
                "name": "Console",
                "mimeType": "application/json",
                "description": "Browser console messages",
            },
            {
                "uri": "page://network",
                "name": "Network",
                "mimeType": "application/json",
                "description": "Network requests log",
            },
        ]

    async def health_check(self) -> bool:
        if self._page is None:
            return True
        try:
            await self._page.evaluate("() => true")
            return True
        except Exception:
            return False
