"""MCP stdio server entry point for Vision analysis.

This wraps the VisionMCPServer as a proper MCP stdio server.
Configuration is loaded from testagent config (vision_api_key, vision_api_url, vision_model).

Usage:
    python -m testagent.mcp_servers.vision_server
"""

import json

import mcp.server as server
import mcp.server.stdio
import mcp.types as types

from testagent.mcp_servers.vision_server.server import VisionMCPServer

_vision_server = VisionMCPServer.from_settings()
mcp = server.Server("vision_server")


@mcp.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=spec["name"],
            description=spec["description"],
            inputSchema=spec["inputSchema"],
        )
        for spec in _vision_server._tools_spec
    ]


@mcp.call_tool()
async def call_tool(
    name: str,
    arguments: dict[str, object] | None,
) -> list[types.TextContent]:
    if arguments is None:
        arguments = {}
    result = await _vision_server.call_tool(name, arguments)
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    return [types.TextContent(type="text", text=text)]


async def main() -> None:
    async with server.stdio.stdio_server() as (read, write):
        await mcp.run(read, write, mcp.create_initialization_options())


if __name__ == "__main__":
    import anyio

    anyio.run(main)
