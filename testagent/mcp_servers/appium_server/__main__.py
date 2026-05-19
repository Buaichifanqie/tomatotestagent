"""MCP stdio server entry point for Appium automation.

This wraps the AppiumMCPServer as a proper MCP stdio server,
allowing the TestAgent MCP registry to connect to it.

Usage:
    python -m testagent.mcp_servers.appium_server
"""

import json
from inspect import iscoroutinefunction
from typing import Any

import mcp.server as server
import mcp.server.stdio
import mcp.types as types

from testagent.mcp_servers.appium_server.server import AppiumMCPServer

_appium_server = AppiumMCPServer(appium_url="http://localhost:4723")
mcp = server.Server("appium_server")


@mcp.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=spec["name"],
            description=spec["description"],
            inputSchema=spec["inputSchema"],
        )
        for spec in _appium_server._tools_spec
    ]


@mcp.call_tool()
async def call_tool(
    name: str,
    arguments: dict[str, object] | None,
) -> list[types.TextContent]:
    if arguments is None:
        arguments = {}
    result = await _appium_server.call_tool(name, arguments)
    text = result if isinstance(result, str) else json.dumps(result)
    return [types.TextContent(type="text", text=text)]


async def main() -> None:
    async with server.stdio.stdio_server() as (read, write):
        await mcp.run(read, write, mcp.create_initialization_options())


if __name__ == "__main__":
    import anyio

    anyio.run(main)
