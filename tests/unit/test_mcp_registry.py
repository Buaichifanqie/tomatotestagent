from __future__ import annotations

from unittest.mock import ANY, AsyncMock, Mock, patch

import pytest

from testagent.common.errors import MCPConnectionError, MCPServerUnavailableError, MCPToolError
from testagent.config.settings import TestAgentSettings
from testagent.gateway.mcp_registry import MCPRegistry, _MCPSession
from testagent.gateway.mcp_router import MCPRouter
from testagent.models.mcp_config import MCPConfig


@pytest.fixture()
def settings() -> TestAgentSettings:
    return TestAgentSettings()


@pytest.fixture()
def mock_tool() -> Mock:
    tool = Mock()
    tool.name = "test_tool"
    tool.description = "A test tool"
    tool.inputSchema = {"type": "object", "properties": {"key": {"type": "string"}}}
    return tool


@pytest.fixture()
def mock_resource() -> Mock:
    resource = Mock()
    resource.uri = "test://resource"
    resource.name = "Test Resource"
    resource.description = "A test resource"
    resource.mimeType = "text/plain"
    return resource


class TestMCPSession:
    """Test _MCPSession lifecycle management"""

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_start_success(self, mock_client_session: Mock, mock_stdio_client: Mock) -> None:
        mock_transport = AsyncMock()
        mock_read = AsyncMock()
        mock_write = AsyncMock()
        mock_transport.__aenter__.return_value = (mock_read, mock_write)
        mock_stdio_client.return_value = mock_transport

        mock_session = AsyncMock()
        mock_session.initialize = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_client_session.return_value = mock_session

        from mcp import StdioServerParameters

        params = StdioServerParameters(command="python")
        session = _MCPSession(params)
        await session.start()

        mock_stdio_client.assert_called_once_with(params)
        mock_client_session.assert_called_once_with(mock_read, mock_write)
        mock_session.initialize.assert_awaited_once()

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_stop_cleans_up(self, mock_client_session: Mock, mock_stdio_client: Mock) -> None:
        mock_transport = AsyncMock()
        mock_transport.__aenter__.return_value = (AsyncMock(), AsyncMock())
        mock_stdio_client.return_value = mock_transport

        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_client_session.return_value = mock_session

        from mcp import StdioServerParameters

        params = StdioServerParameters(command="python")
        session = _MCPSession(params)
        await session.start()
        await session.stop()

        mock_session.__aexit__.assert_awaited_once()
        mock_transport.__aexit__.assert_awaited_once()

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_call_tool_raises_when_not_started(self, mock_client_session: Mock, mock_stdio_client: Mock) -> None:
        mock_session = AsyncMock()
        mock_session.__aenter__.return_value = mock_session
        mock_client_session.return_value = mock_session

        from mcp import StdioServerParameters

        params = StdioServerParameters(command="python")
        session = _MCPSession(params)

        with pytest.raises(MCPServerUnavailableError, match="Session not initialized"):
            await session.call_tool("test", {})

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_health_check_returns_false_when_not_started(
        self, mock_client_session: Mock, mock_stdio_client: Mock
    ) -> None:
        from mcp import StdioServerParameters

        params = StdioServerParameters(command="python")
        session = _MCPSession(params)

        result = await session.health_check()
        assert result is False


class TestMCPRegistry:
    """Test MCPRegistry register/unregister/lookup and lifecycle"""

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_register_success(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])

        config = MCPConfig(server_name="test_server", command="python")
        registry = MCPRegistry(settings)
        info = await registry.register(config)

        assert info.name == "test_server"
        assert info.command == "python"
        assert info.status == "healthy"
        assert len(info.tools) == 1
        assert info.tools[0]["name"] == "test_tool"
        assert len(info.resources) == 1
        assert info.resources[0]["uri"] == "test://resource"

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_register_duplicate_raises(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])

        config = MCPConfig(server_name="dup_server", command="python")
        registry = MCPRegistry(settings)
        await registry.register(config)

        with pytest.raises(MCPConnectionError, match="already registered"):
            await registry.register(config)

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_register_start_failure_sets_unavailable(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
    ) -> None:
        mock_transport = AsyncMock()
        mock_transport.__aenter__.side_effect = RuntimeError("Connection refused")
        mock_stdio_client.return_value = mock_transport

        config = MCPConfig(server_name="failing_server", command="python")
        registry = MCPRegistry(settings)

        with pytest.raises(MCPConnectionError, match="Failed to start"):
            await registry.register(config)

        with pytest.raises(MCPServerUnavailableError, match="not found"):
            await registry.lookup("failing_server")

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_unregister_removes_server(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])

        config = MCPConfig(server_name="to_remove", command="python")
        registry = MCPRegistry(settings)
        await registry.register(config)
        await registry.unregister("to_remove")

        with pytest.raises(MCPServerUnavailableError, match="not found"):
            await registry.lookup("to_remove")

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_unregister_nonexistent_raises(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
    ) -> None:
        registry = MCPRegistry(settings)

        with pytest.raises(MCPServerUnavailableError, match="not found"):
            await registry.unregister("nonexistent")

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_lookup_returns_server_info(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])

        config = MCPConfig(server_name="lookup_me", command="python")
        registry = MCPRegistry(settings)
        await registry.register(config)

        info = await registry.lookup("lookup_me")
        assert info.name == "lookup_me"
        assert info.status == "healthy"

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_lookup_nonexistent_raises(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
    ) -> None:
        registry = MCPRegistry(settings)

        with pytest.raises(MCPServerUnavailableError, match="not found"):
            await registry.lookup("i_dont_exist")

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_list_servers(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])

        config1 = MCPConfig(server_name="server_a", command="python")
        config2 = MCPConfig(server_name="server_b", command="node")

        registry = MCPRegistry(settings)
        await registry.register(config1)
        await registry.register(config2)

        servers = await registry.list_servers()
        assert len(servers) == 2
        names = {s.name for s in servers}
        assert names == {"server_a", "server_b"}

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_call_tool_success(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        mock_session = _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])

        mock_result = Mock()
        mock_result.isError = False
        mock_result.content = [{"type": "text", "text": "Success"}]
        mock_session.call_tool = AsyncMock(return_value=mock_result)

        config = MCPConfig(server_name="tool_server", command="python")
        registry = MCPRegistry(settings)
        await registry.register(config)

        result = await registry.call_tool("tool_server", "test_tool", {"key": "value"})
        assert result == [{"type": "text", "text": "Success"}]
        mock_session.call_tool.assert_awaited_once_with("test_tool", {"key": "value"})

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_call_tool_error_response(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        mock_session = _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])

        mock_result = Mock()
        mock_result.isError = True
        mock_result.content = [{"type": "text", "text": "Something went wrong"}]
        mock_session.call_tool = AsyncMock(return_value=mock_result)

        config = MCPConfig(server_name="err_server", command="python")
        registry = MCPRegistry(settings)
        await registry.register(config)

        with pytest.raises(MCPToolError, match="returned error"):
            await registry.call_tool("err_server", "test_tool", {})

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_call_tool_unavailable_server(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
    ) -> None:
        registry = MCPRegistry(settings)

        with pytest.raises(MCPServerUnavailableError, match="not found"):
            await registry.call_tool("ghost_server", "any_tool", {})

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_convert_args_dict_to_list(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])

        config = MCPConfig(
            server_name="args_test",
            command="python",
            args={"host": "localhost", "port": 8080},
        )
        registry = MCPRegistry(settings)
        info = await registry.register(config)

        assert "--host" in info.args
        assert "localhost" in info.args
        assert "--port" in info.args
        assert "8080" in info.args


class TestHealthMonitor:
    """Test health check status transitions and auto-restart"""

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_health_check_healthy(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        mock_session = _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])
        mock_session.list_tools = AsyncMock()

        config = MCPConfig(server_name="healthy_srv", command="python")
        registry = MCPRegistry(settings)
        await registry.register(config)

        await registry._check_all_servers()

        info = await registry.lookup("healthy_srv")
        assert info.status == "healthy"
        assert registry._failure_counts["healthy_srv"] == 0

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_health_check_consecutive_failures_to_unhealthy(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        mock_session = _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])

        # After registration, the session is stored; simulate health check failures
        mock_session.health_check = AsyncMock(return_value=False)

        config = MCPConfig(server_name="failing_srv", command="python")
        registry = MCPRegistry(settings)
        await registry.register(config)

        # Restore the original list_tools since we need it for health check
        mock_session.list_tools = AsyncMock(side_effect=RuntimeError("Not responding"))
        mock_session.health_check = AsyncMock(return_value=False)

        # First check - failure count 1
        await registry._check_all_servers()
        assert registry._failure_counts["failing_srv"] == 1

        info = await registry.lookup("failing_srv")
        assert info.status == "healthy"

        # Second check - failure count 2
        await registry._check_all_servers()
        assert registry._failure_counts["failing_srv"] == 2

        # Third check - failure count 3, should trigger unhealthy + restart attempt
        with patch.object(registry, "_restart_server", new=AsyncMock(return_value=False)) as mock_restart:
            await registry._check_all_servers()
            assert registry._failure_counts["failing_srv"] == 3
            mock_restart.assert_awaited_once_with("failing_srv")

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_restart_server_success(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])

        config = MCPConfig(server_name="restart_srv", command="python")
        registry = MCPRegistry(settings)
        await registry.register(config)

        # Stop the current session to simulate a crash
        old_session = registry._sessions.pop("restart_srv")
        await old_session.stop()

        # Set up new mock for restart
        new_mock_session = AsyncMock()
        new_mock_session.initialize = AsyncMock()
        new_mock_session.__aenter__.return_value = new_mock_session

        tools_result = Mock()
        tools_result.tools = [mock_tool]
        new_mock_session.list_tools = AsyncMock(return_value=tools_result)

        resources_result = Mock()
        resources_result.resources = [mock_resource]
        new_mock_session.list_resources = AsyncMock(return_value=resources_result)

        mock_client_session.return_value = new_mock_session

        # Set server to unhealthy to trigger restart
        async with registry._lock:
            registry._servers["restart_srv"].status = "unhealthy"

        result = await registry._restart_server("restart_srv")
        assert result is True

        info = await registry.lookup("restart_srv")
        assert info.status == "healthy"
        assert registry._failure_counts["restart_srv"] == 0
        assert registry._restart_counts["restart_srv"] == 1

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_restart_server_exhausts_retries(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])

        config = MCPConfig(server_name="exhaust_srv", command="python")
        registry = MCPRegistry(settings)
        await registry.register(config)

        async with registry._lock:
            registry._servers["exhaust_srv"].status = "unhealthy"
            registry._restart_counts["exhaust_srv"] = 3  # Already exhausted

        result = await registry._restart_server("exhaust_srv")
        assert result is False

        info = await registry.lookup("exhaust_srv")
        assert info.status == "unavailable"

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_restart_server_failure_sets_unhealthy(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])

        config = MCPConfig(server_name="fail_restart_srv", command="python")
        registry = MCPRegistry(settings)
        await registry.register(config)

        async with registry._lock:
            registry._servers["fail_restart_srv"].status = "unhealthy"

        # Make the stdio_client fail during restart
        mock_transport = AsyncMock()
        mock_transport.__aenter__.side_effect = RuntimeError("Restart failed")
        mock_stdio_client.return_value = mock_transport

        result = await registry._restart_server("fail_restart_srv")
        assert result is False

        info = await registry.lookup("fail_restart_srv")
        assert info.status == "unhealthy"

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_monitor_start_stop(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])

        registry = MCPRegistry(settings)
        await registry.start_health_monitor()

        assert registry._monitor_task is not None
        assert not registry._monitor_task.done()

        await registry.stop_health_monitor()
        assert registry._monitor_task is None


class TestMCPRouter:
    """Test MCPRouter audit logging and routing"""

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_route_call_success(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        mock_session = _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])

        mock_result = Mock()
        mock_result.isError = False
        mock_result.content = [{"type": "text", "text": "Success"}]
        mock_session.call_tool = AsyncMock(return_value=mock_result)

        config = MCPConfig(server_name="router_test", command="python")
        registry = MCPRegistry(settings)
        await registry.register(config)

        router = MCPRouter(registry)
        result = await router.route_call("router_test", "test_tool", {"key": "val"}, caller_id="test-user")

        assert result == [{"type": "text", "text": "Success"}]
        mock_session.call_tool.assert_awaited_once_with("test_tool", {"key": "val"})

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_route_call_tool_not_found(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])

        config = MCPConfig(server_name="no_tool_srv", command="python")
        registry = MCPRegistry(settings)
        await registry.register(config)

        router = MCPRouter(registry)
        with pytest.raises(MCPToolError, match="not found"):
            await router.route_call("no_tool_srv", "nonexistent_tool", {})

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_route_call_unavailable_server(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
    ) -> None:
        registry = MCPRegistry(settings)
        router = MCPRouter(registry)

        with pytest.raises(MCPServerUnavailableError, match="not found"):
            await router.route_call("ghost", "tool", {})

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_audit_log_before_and_after(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        mock_session = _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])
        mock_session.call_tool = AsyncMock(return_value=Mock(isError=False, content=[{"type": "text", "text": "done"}]))

        config = MCPConfig(server_name="audit_srv", command="python")
        registry = MCPRegistry(settings)
        await registry.register(config)

        router = MCPRouter(registry)

        with patch.object(router, "_logger") as mock_logger:
            await router.route_call("audit_srv", "test_tool", {"x": 1}, caller_id="tester")

        mock_logger.info.assert_any_call(
            "MCP tool call started",
            extra={
                "extra_data": {
                    "who": "tester",
                    "when": ANY,
                    "server": "audit_srv",
                    "tool": "test_tool",
                    "args_snapshot": "{'x': 1}",
                }
            },
        )
        mock_logger.info.assert_any_call(
            "MCP tool call completed",
            extra={
                "extra_data": {
                    "who": "tester",
                    "when": ANY,
                    "server": "audit_srv",
                    "tool": "test_tool",
                    "result_summary": "[{'type': 'text', 'text': 'done'}]",
                }
            },
        )

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_summarize_result(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        assert MCPRouter._summarize_result(None) == "None"
        assert MCPRouter._summarize_result([1, 2]) == "[1, 2]"
        assert MCPRouter._summarize_result([1, 2, 3, 4, 5]) == "[5 items]"
        assert MCPRouter._summarize_result({"a": 1, "b": 2}) == "{a, b}"


class TestDegradedHandling:
    """Test MCP Server unavailable degradation"""

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_call_tool_on_unavailable_raises(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])

        config = MCPConfig(server_name="degraded_srv", command="python")
        registry = MCPRegistry(settings)
        await registry.register(config)

        # Manually set status to unavailable
        async with registry._lock:
            registry._servers["degraded_srv"].status = "unavailable"

        with pytest.raises(MCPServerUnavailableError, match="unavailable"):
            await registry.call_tool("degraded_srv", "test_tool", {})

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_lookup_after_full_degradation(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])

        config = MCPConfig(server_name="degraded_lookup", command="python")
        registry = MCPRegistry(settings)
        await registry.register(config)

        async with registry._lock:
            registry._servers["degraded_lookup"].status = "unavailable"

        # lookup still returns info even when unavailable
        info = await registry.lookup("degraded_lookup")
        assert info.status == "unavailable"

    @patch("testagent.gateway.mcp_registry.stdio_client")
    @patch("testagent.gateway.mcp_registry.ClientSession")
    async def test_health_monitor_skips_unavailable(
        self,
        mock_client_session: Mock,
        mock_stdio_client: Mock,
        settings: TestAgentSettings,
        mock_tool: Mock,
        mock_resource: Mock,
    ) -> None:
        mock_session = _setup_session_mocks(mock_client_session, mock_stdio_client, [mock_tool], [mock_resource])

        config = MCPConfig(server_name="skip_unavail", command="python")
        registry = MCPRegistry(settings)
        await registry.register(config)

        async with registry._lock:
            registry._servers["skip_unavail"].status = "unavailable"

        # Even if health check would fail, it should be skipped
        mock_session.health_check = AsyncMock(side_effect=AssertionError("Should not be called"))

        await registry._check_all_servers()

        info = await registry.lookup("skip_unavail")
        assert info.status == "unavailable"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _setup_session_mocks(
    mock_client_session: Mock,
    mock_stdio_client: Mock,
    tools: list[Mock],
    resources: list[Mock],
) -> Mock:
    """Configure common mocks for stdio_client and ClientSession.

    Returns the mocked session instance so callers can attach additional
    behaviors (e.g. ``call_tool`` return values).
    """
    mock_transport = AsyncMock()
    mock_read = AsyncMock()
    mock_write = AsyncMock()
    mock_transport.__aenter__.return_value = (mock_read, mock_write)
    mock_stdio_client.return_value = mock_transport

    mock_session = AsyncMock()
    mock_session.initialize = AsyncMock()

    tools_result = Mock()
    tools_result.tools = tools
    mock_session.list_tools = AsyncMock(return_value=tools_result)

    resources_result = Mock()
    resources_result.resources = resources
    mock_session.list_resources = AsyncMock(return_value=resources_result)

    mock_session.__aenter__.return_value = mock_session
    mock_client_session.return_value = mock_session

    return mock_session
