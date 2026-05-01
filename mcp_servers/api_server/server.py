from __future__ import annotations

import json
from typing import Any, ClassVar

import httpx

from testagent.mcp_servers.api_server.tools import (
    api_compare_response,
    api_request,
    api_validate_schema,
)
from testagent.mcp_servers.base import BaseMCPServer


class APIMCPServer(BaseMCPServer):
    server_name = "api_server"

    _tools_spec: ClassVar[list[dict[str, object]]] = [
        {
            "name": "api_request",
            "description": "Send HTTP request, return {status_code, headers, body, duration_ms}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "description": "HTTP method (GET/POST/PUT/DELETE/PATCH)"},
                    "url": {"type": "string", "description": "Request URL"},
                    "headers": {"type": "object", "description": "Request headers"},
                    "body": {"type": "object", "description": "Request body JSON"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds, default 30"},
                },
                "required": ["method", "url"],
            },
        },
        {
            "name": "api_validate_schema",
            "description": "Validate response body against JSON Schema, return {valid, errors[]}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "response_body": {"type": "object", "description": "Response body to validate"},
                    "schema": {"type": "object", "description": "JSON Schema object"},
                    "schema_url": {"type": "string", "description": "JSON Schema URL (alternative to schema)"},
                },
                "required": ["response_body"],
            },
        },
        {
            "name": "api_compare_response",
            "description": "Compare two responses, return {match, diff_fields[]}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "response_a": {"type": "object", "description": "First response body"},
                    "response_b": {"type": "object", "description": "Second response body"},
                    "ignore_fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of field paths to ignore during comparison",
                    },
                },
                "required": ["response_a", "response_b"],
            },
        },
    ]

    _tool_registry: ClassVar[dict[str, Any]] = {
        "api_request": api_request,
        "api_validate_schema": api_validate_schema,
        "api_compare_response": api_compare_response,
    }

    async def list_tools(self) -> list[dict[str, object]]:
        return self._tools_spec

    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
        tool = self._tool_registry.get(tool_name)
        if tool is None:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        try:
            from inspect import iscoroutinefunction

            if iscoroutinefunction(tool):
                result = await tool(**arguments)
            else:
                result = tool(**arguments)
            if isinstance(result, (dict, list)):
                return json.dumps(result)
            return str(result)
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def list_resources(self) -> list[dict[str, object]]:
        return []

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as client:
                response = await client.get("https://httpbin.org/get")
                return response.status_code == 200
        except Exception:
            return False
