from __future__ import annotations

from abc import ABC, abstractmethod


class BaseMCPServer(ABC):
    """Base class for all MCP Server implementations.

    All MCP Server implementations must inherit this class and
    implement the following four methods:
    - list_tools: return the list of available tools
    - call_tool: call a specific tool with given arguments
    - list_resources: return the list of available resources
    - health_check: return True if the server is healthy
    """

    server_name: str

    @abstractmethod
    async def list_tools(self) -> list[dict[str, object]]:
        """Return the list of tools provided by this server."""

    @abstractmethod
    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
        """Call a specific tool and return the result."""

    @abstractmethod
    async def list_resources(self) -> list[dict[str, object]]:
        """Return the list of resources provided by this server."""

    @abstractmethod
    async def health_check(self) -> bool:
        """Perform a health check. Return True if the server is healthy."""
