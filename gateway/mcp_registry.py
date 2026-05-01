from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

if TYPE_CHECKING:
    from testagent.config.settings import TestAgentSettings
    from testagent.models.mcp_config import MCPConfig

from testagent.common import get_logger
from testagent.common.errors import MCPConnectionError, MCPServerUnavailableError, MCPToolError

_logger = get_logger(__name__)

_HEARTBEAT_INTERVAL = 30
_MAX_FAILURE_COUNT = 3
_MAX_RESTART_COUNT = 3


@dataclass
class MCPServerInfo:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    status: str = "starting"  # starting / healthy / unhealthy / unavailable
    tools: list[dict[str, object]] = field(default_factory=list)
    resources: list[dict[str, object]] = field(default_factory=list)


class _MCPSession:
    def __init__(self, params: StdioServerParameters) -> None:
        self._params = params
        self._transport: Any = None
        self._session: ClientSession | None = None

    async def start(self) -> None:
        self._transport = stdio_client(self._params)
        self._read, self._write = await self._transport.__aenter__()
        self._session = ClientSession(self._read, self._write)
        await self._session.__aenter__()
        await self._session.initialize()

    async def stop(self) -> None:
        if self._session is not None:
            with contextlib.suppress(Exception):
                await self._session.__aexit__(None, None, None)
            self._session = None
        if self._transport is not None:
            with contextlib.suppress(Exception):
                await self._transport.__aexit__(None, None, None)
            self._transport = None

    async def list_tools(self) -> Any:
        if self._session is None:
            raise MCPServerUnavailableError("Session not initialized")
        return await self._session.list_tools()

    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> Any:
        if self._session is None:
            raise MCPServerUnavailableError("Session not initialized")
        return await self._session.call_tool(tool_name, arguments)

    async def list_resources(self) -> Any:
        if self._session is None:
            raise MCPServerUnavailableError("Session not initialized")
        return await self._session.list_resources()

    async def health_check(self) -> bool:
        if self._session is None:
            return False
        try:
            await self._session.list_tools()
            return True
        except Exception:
            return False


class MCPRegistry:
    """MCP Server registry with health monitoring and auto-restart."""

    def __init__(self, settings: TestAgentSettings) -> None:
        self._settings = settings
        self._servers: dict[str, MCPServerInfo] = {}
        self._sessions: dict[str, _MCPSession] = {}
        self._failure_counts: dict[str, int] = {}
        self._restart_counts: dict[str, int] = {}
        self._monitor_task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._logger = _logger

    @staticmethod
    def _convert_args(args: dict[str, object] | None) -> list[str]:
        if not args:
            return []
        result: list[str] = []
        for k, v in args.items():
            result.append(f"--{k}")
            result.append(str(v))
        return result

    @staticmethod
    def _tool_to_dict(tool: Any) -> dict[str, object]:
        return {
            "name": tool.name,
            "description": tool.description,
            "input_schema": tool.inputSchema,
        }

    @staticmethod
    def _resource_to_dict(resource: Any) -> dict[str, object]:
        return {
            "uri": resource.uri,
            "name": resource.name,
            "description": getattr(resource, "description", None),
            "mime_type": getattr(resource, "mimeType", None),
        }

    async def register(self, config: MCPConfig) -> MCPServerInfo:
        async with self._lock:
            if config.server_name in self._servers:
                raise MCPConnectionError(
                    message=f"MCP Server '{config.server_name}' already registered",
                    code="MCP_SERVER_ALREADY_EXISTS",
                )

            cmd_args = self._convert_args(config.args)
            env = {k: str(v) for k, v in (config.env or {}).items()}

            info = MCPServerInfo(
                name=config.server_name,
                command=config.command,
                args=cmd_args,
                env=env,
                status="starting",
            )
            self._servers[config.server_name] = info

            params = StdioServerParameters(
                command=config.command,
                args=cmd_args,
                env=env if env else None,
            )
            session = _MCPSession(params)

            try:
                await session.start()

                tools_result = await session.list_tools()
                info.tools = [self._tool_to_dict(t) for t in tools_result.tools]

                resources_result = await session.list_resources()
                info.resources = [self._resource_to_dict(r) for r in resources_result.resources]

                info.status = "healthy"
                self._sessions[config.server_name] = session
                self._failure_counts[config.server_name] = 0
                self._restart_counts[config.server_name] = 0
                self._logger.info(
                    "MCP Server registered",
                    extra={"extra_data": {"server": config.server_name, "tools_count": len(info.tools)}},
                )
            except Exception as exc:
                self._servers.pop(config.server_name, None)
                await session.stop()
                self._logger.error(
                    "Failed to register MCP Server",
                    extra={"extra_data": {"server": config.server_name, "error": str(exc)}},
                )
                raise MCPConnectionError(
                    message=f"Failed to start MCP Server '{config.server_name}': {exc}",
                    code="MCP_SERVER_START_FAILED",
                    details={"server": config.server_name},
                ) from exc

            return info

    async def unregister(self, server_name: str) -> None:
        async with self._lock:
            if server_name not in self._servers:
                raise MCPServerUnavailableError(
                    message=f"MCP Server '{server_name}' not found",
                    code="MCP_SERVER_NOT_FOUND",
                    details={"server": server_name},
                )

            session = self._sessions.pop(server_name, None)
            if session is not None:
                await session.stop()

            self._servers.pop(server_name, None)
            self._failure_counts.pop(server_name, None)
            self._restart_counts.pop(server_name, None)

            self._logger.info(
                "MCP Server unregistered",
                extra={"extra_data": {"server": server_name}},
            )

    async def lookup(self, server_name: str) -> MCPServerInfo:
        info = self._servers.get(server_name)
        if info is None:
            raise MCPServerUnavailableError(
                message=f"MCP Server '{server_name}' not found",
                code="MCP_SERVER_NOT_FOUND",
                details={"server": server_name},
            )
        return info

    async def list_servers(self) -> list[MCPServerInfo]:
        return list(self._servers.values())

    async def call_tool(self, server_name: str, tool_name: str, args: dict[str, object]) -> Any:
        info = await self.lookup(server_name)
        if info.status in ("unavailable",):
            raise MCPServerUnavailableError(
                message=f"MCP Server '{server_name}' is unavailable",
                code="MCP_SERVER_UNAVAILABLE",
                details={"server": server_name, "status": info.status},
            )

        session = self._sessions.get(server_name)
        if session is None:
            raise MCPServerUnavailableError(
                message=f"MCP Server '{server_name}' session not initialized",
                code="MCP_SERVER_SESSION_MISSING",
                details={"server": server_name},
            )

        try:
            result = await session.call_tool(tool_name, args)

            if result.isError:
                raise MCPToolError(
                    message=f"Tool '{tool_name}' on server '{server_name}' returned error",
                    code="MCP_TOOL_ERROR",
                    details={"server": server_name, "tool": tool_name, "error": str(result.content)},
                )

            return result.content
        except MCPServerUnavailableError:
            raise
        except MCPToolError:
            raise
        except Exception as exc:
            raise MCPToolError(
                message=f"Failed to call tool '{tool_name}' on server '{server_name}': {exc}",
                code="MCP_TOOL_CALL_FAILED",
                details={"server": server_name, "tool": tool_name},
            ) from exc

    async def start_health_monitor(self) -> None:
        """Start background health monitor. Checks every 30s, 3 failures triggers restart."""

        async def _monitor_loop() -> None:
            self._logger.info("Health monitor started")
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
                await self._check_all_servers()

        self._monitor_task = asyncio.create_task(_monitor_loop())

    async def stop_health_monitor(self) -> None:
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task
            self._monitor_task = None
            self._logger.info("Health monitor stopped")

    async def _check_all_servers(self) -> None:
        server_names = list(self._servers.keys())
        for name in server_names:
            async with self._lock:
                info = self._servers.get(name)
                if info is None:
                    continue
                if info.status == "unavailable":
                    continue

            session = self._sessions.get(name)
            if session is None:
                await self._record_failure(name)
                continue

            healthy = await session.health_check()
            if healthy:
                async with self._lock:
                    self._failure_counts[name] = 0
                    if info is not None:
                        info.status = "healthy"
            else:
                await self._record_failure(name)

    async def _record_failure(self, server_name: str) -> None:
        async with self._lock:
            info = self._servers.get(server_name)
            if info is None:
                return

            self._failure_counts[server_name] = self._failure_counts.get(server_name, 0) + 1
            count = self._failure_counts[server_name]

            self._logger.warning(
                "MCP Server health check failed",
                extra={"extra_data": {"server": server_name, "failure_count": count}},
            )

            if count >= _MAX_FAILURE_COUNT:
                info.status = "unhealthy"
                self._logger.error(
                    "MCP Server marked unhealthy, attempting restart",
                    extra={"extra_data": {"server": server_name}},
                )
                await self._restart_server(server_name)

    async def _restart_server(self, server_name: str) -> bool:
        restart_count = self._restart_counts.get(server_name, 0)
        if restart_count >= _MAX_RESTART_COUNT:
            async with self._lock:
                info = self._servers.get(server_name)
                if info is not None:
                    info.status = "unavailable"
            self._logger.error(
                "MCP Server restart limit exceeded",
                extra={"extra_data": {"server": server_name, "max_restarts": _MAX_RESTART_COUNT}},
            )
            return False

        self._restart_counts[server_name] = restart_count + 1

        old_session = self._sessions.pop(server_name, None)
        if old_session is not None:
            await old_session.stop()

        async with self._lock:
            info = self._servers.get(server_name)
            if info is None:
                return False
            info.status = "starting"

        try:
            params = StdioServerParameters(
                command=info.command,
                args=info.args,
                env=info.env if info.env else None,
            )
            new_session = _MCPSession(params)
            await new_session.start()

            tools_result = await new_session.list_tools()
            tools = [self._tool_to_dict(t) for t in tools_result.tools]

            resources_result = await new_session.list_resources()
            resources = [self._resource_to_dict(r) for r in resources_result.resources]

            async with self._lock:
                info.tools = tools
                info.resources = resources
                info.status = "healthy"
                self._sessions[server_name] = new_session
                self._failure_counts[server_name] = 0

            self._logger.info(
                "MCP Server restarted successfully",
                extra={"extra_data": {"server": server_name, "restart_attempt": self._restart_counts[server_name]}},
            )
            return True
        except Exception as exc:
            async with self._lock:
                info.status = "unhealthy"
            self._logger.error(
                "MCP Server restart failed",
                extra={"extra_data": {"server": server_name, "error": str(exc)}},
            )
            return False
