from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from testagent.gateway.mcp_registry import MCPRegistry

from testagent.common import get_logger
from testagent.common.errors import MCPToolError

_logger = get_logger(__name__)


class MCPRouter:
    """MCP tool call router with audit logging."""

    def __init__(self, registry: MCPRegistry) -> None:
        self._registry = registry
        self._logger = _logger

    async def route_call(
        self,
        server_name: str,
        tool_name: str,
        args: dict[str, object],
        caller_id: str = "unknown",
    ) -> Any:
        """Route and execute an MCP tool call with audit logging.

        Flow:
        1. registry.lookup(server_name)
        2. Verify tool_name is in server.tools
        3. Audit log: who, when, what, args
        4. registry.call_tool(server_name, tool_name, args)
        5. Audit log: result_summary
        6. Return result
        """
        info = await self._registry.lookup(server_name)

        tool_names = [t["name"] for t in info.tools]
        if tool_name not in tool_names:
            raise MCPToolError(
                message=f"Tool '{tool_name}' not found on server '{server_name}'",
                code="MCP_TOOL_NOT_FOUND",
                details={
                    "server": server_name,
                    "requested_tool": tool_name,
                    "available_tools": tool_names,
                },
            )

        self._logger.info(
            "MCP tool call started",
            extra={
                "extra_data": {
                    "who": caller_id,
                    "when": datetime.now(UTC).isoformat(),
                    "server": server_name,
                    "tool": tool_name,
                    "args_snapshot": str(args)[:500],
                }
            },
        )

        result = await self._registry.call_tool(server_name, tool_name, args)

        self._logger.info(
            "MCP tool call completed",
            extra={
                "extra_data": {
                    "who": caller_id,
                    "when": datetime.now(UTC).isoformat(),
                    "server": server_name,
                    "tool": tool_name,
                    "result_summary": self._summarize_result(result),
                }
            },
        )

        return result

    @staticmethod
    def _summarize_result(result: Any) -> str:
        if result is None:
            return "None"
        if isinstance(result, list):
            if len(result) > 3:
                return f"[{len(result)} items]"
            return str(result)[:200]
        if isinstance(result, dict):
            return f"{{{', '.join(list(result.keys())[:5])}}}"
        return str(result)[:200]
