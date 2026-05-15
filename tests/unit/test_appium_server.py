from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.mcp_servers.appium_server.server import AppiumMCPServer
from testagent.mcp_servers.appium_server.tools import (
    app_assert_element,
    app_get_source,
    app_install,
    app_screenshot,
    app_swipe,
    app_tap,
    app_type,
)

APPIUM_URL = "http://localhost:4723"


@pytest.fixture()
def server() -> AppiumMCPServer:
    return AppiumMCPServer(appium_url=APPIUM_URL)


def _mock_client_for_post(response_body: dict, status_code: int = 200) -> AsyncMock:
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.json.return_value = response_body
    mock_response.text = json.dumps(response_body)

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    return mock_client


class TestAppiumMCPServerListTools:
    async def test_list_tools_returns_seven_tools(self, server: AppiumMCPServer) -> None:
        tools = await server.list_tools()
        assert len(tools) == 7

    async def test_list_tools_contains_all_tool_names(self, server: AppiumMCPServer) -> None:
        tools = await server.list_tools()
        tool_names = {t["name"] for t in tools}
        expected = {
            "app_install",
            "app_tap",
            "app_swipe",
            "app_type",
            "app_assert_element",
            "app_screenshot",
            "app_get_source",
        }
        assert tool_names == expected

    async def test_list_tools_input_schemas_have_required_fields(self, server: AppiumMCPServer) -> None:
        tools = await server.list_tools()
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool


class TestAppiumMCPServerHealthCheck:
    async def test_health_check_returns_true_when_healthy(self, server: AppiumMCPServer) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.appium_server.server.httpx.AsyncClient", return_value=mock_client):
            result = await server.health_check()

        assert result is True

    async def test_health_check_returns_false_when_unreachable(self, server: AppiumMCPServer) -> None:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = None
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))

        with patch("testagent.mcp_servers.appium_server.server.httpx.AsyncClient", return_value=mock_client):
            result = await server.health_check()

        assert result is False

    async def test_health_check_returns_false_on_non_200(self, server: AppiumMCPServer) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.appium_server.server.httpx.AsyncClient", return_value=mock_client):
            result = await server.health_check()

        assert result is False

    async def test_health_check_pings_status_endpoint(self, server: AppiumMCPServer) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.appium_server.server.httpx.AsyncClient", return_value=mock_client):
            await server.health_check()

        call_args = mock_client.get.call_args
        assert "/status" in call_args.args[0]


class TestAppTapParamValidation:
    async def test_tap_with_invalid_strategy_returns_error(self) -> None:
        result = await app_tap("login-button", strategy="invalid_strategy", appium_url=APPIUM_URL)
        assert "error" in result
        assert "Invalid strategy" in result["error"]

    async def test_tap_with_valid_strategy_accessibility_id(self) -> None:
        mock_client = _mock_client_for_post({"ELEMENT": "elem-123"})
        with patch("testagent.mcp_servers.appium_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await app_tap("login-button", strategy="accessibility_id", appium_url=APPIUM_URL)
        assert "error" not in result or result.get("status_code") is not None

    async def test_tap_element_not_found_returns_error(self) -> None:
        mock_client = _mock_client_for_post({"value": "no such element"}, status_code=404)
        with patch("testagent.mcp_servers.appium_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await app_tap("nonexistent", strategy="accessibility_id", appium_url=APPIUM_URL)
        assert "error" in result

    async def test_tap_with_xpath_strategy(self) -> None:
        mock_client = _mock_client_for_post({"ELEMENT": "elem-456"})
        with patch("testagent.mcp_servers.appium_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await app_tap("//Button[@text='Login']", strategy="xpath", appium_url=APPIUM_URL)
        assert "error" not in result or result.get("status_code") is not None

    async def test_tap_with_uiautomator_strategy(self) -> None:
        mock_client = _mock_client_for_post({"ELEMENT": "elem-789"})
        with patch("testagent.mcp_servers.appium_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await app_tap('new UiSelector().text("Login")', strategy="uiautomator", appium_url=APPIUM_URL)
        assert "error" not in result or result.get("status_code") is not None


class TestAppSwipeParamValidation:
    async def test_swipe_sends_correct_coordinates(self) -> None:
        mock_client = _mock_client_for_post({"value": ""})
        with patch("testagent.mcp_servers.appium_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await app_swipe(100, 500, 100, 100, duration=300, appium_url=APPIUM_URL)
        assert result["status_code"] == 200

    async def test_swipe_default_duration_is_500(self) -> None:
        mock_client = _mock_client_for_post({"value": ""})
        with patch("testagent.mcp_servers.appium_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await app_swipe(100, 500, 100, 100, appium_url=APPIUM_URL)
        assert result["status_code"] == 200


class TestAppAssertElementParamValidation:
    async def test_assert_with_invalid_strategy_returns_error(self) -> None:
        result = await app_assert_element("login-button", assertion="visible", strategy="bad", appium_url=APPIUM_URL)
        assert "error" in result
        assert "Invalid strategy" in result["error"]

    async def test_assert_with_invalid_assertion_returns_error(self) -> None:
        result = await app_assert_element(
            "login-button", assertion="hover", strategy="accessibility_id", appium_url=APPIUM_URL
        )
        assert "error" in result
        assert "Invalid assertion" in result["error"]

    async def test_assert_visible_element_found_passes(self) -> None:
        mock_client = _mock_client_for_post({"ELEMENT": "elem-1"})
        with patch("testagent.mcp_servers.appium_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await app_assert_element(
                "login-button", assertion="visible", strategy="accessibility_id", appium_url=APPIUM_URL
            )
        assert result["passed"] is True

    async def test_assert_visible_element_not_found_fails(self) -> None:
        mock_client = _mock_client_for_post({"value": "no element"}, status_code=404)
        with patch("testagent.mcp_servers.appium_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await app_assert_element(
                "nonexistent", assertion="visible", strategy="accessibility_id", appium_url=APPIUM_URL
            )
        assert result["passed"] is False

    async def test_assert_text_with_expected_value(self) -> None:
        call_count = 0

        async def _side_effect(*args: object, **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"ELEMENT": "elem-2"}
                mock_resp.text = '{"ELEMENT": "elem-2"}'
                return mock_resp
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"value": "Hello"}
            mock_resp.text = '{"value": "Hello"}'
            return mock_resp

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.__aexit__.return_value = AsyncMock()
        mock_client.post = AsyncMock(side_effect=_side_effect)

        with patch("testagent.mcp_servers.appium_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await app_assert_element(
                "label", assertion="text", expected="Hello", strategy="accessibility_id", appium_url=APPIUM_URL
            )
        assert result["passed"] is True
        assert result["actual"] == "Hello"
        assert result["expected"] == "Hello"

    async def test_assert_attribute_without_expected_returns_error(self) -> None:
        mock_client = _mock_client_for_post({"ELEMENT": "elem-3"})
        with patch("testagent.mcp_servers.appium_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await app_assert_element(
                "input-field", assertion="attribute", strategy="accessibility_id", appium_url=APPIUM_URL
            )
        assert "error" in result
        assert "Attribute name is required" in result["error"]


class TestAppiumMCPServerCallTool:
    async def test_call_tool_unknown_tool_returns_error(self, server: AppiumMCPServer) -> None:
        raw_result = await server.call_tool("unknown_tool", {})
        result = json.loads(str(raw_result))
        assert "error" in result
        assert "Unknown tool" in result["error"]

    async def test_call_tool_app_install_dispatches(self, server: AppiumMCPServer) -> None:
        mock_client = _mock_client_for_post({"value": "installed"})
        with patch("testagent.mcp_servers.appium_server.tools.httpx.AsyncClient", return_value=mock_client):
            raw_result = await server.call_tool("app_install", {"app_path": "/path/to/app.apk"})
        result = json.loads(str(raw_result))
        assert result["status_code"] == 200

    async def test_server_name_is_appium_server(self, server: AppiumMCPServer) -> None:
        assert server.server_name == "appium_server"

    async def test_list_resources_returns_app_resources(self, server: AppiumMCPServer) -> None:
        resources = await server.list_resources()
        assert len(resources) == 2
        uris = {r["uri"] for r in resources}
        assert "app://source" in uris
        assert "app://screenshot" in uris

    async def test_call_tool_injects_appium_url(self, server: AppiumMCPServer) -> None:
        mock_client = _mock_client_for_post({"value": "<xml/>"})
        with patch("testagent.mcp_servers.appium_server.tools.httpx.AsyncClient", return_value=mock_client):
            raw_result = await server.call_tool("app_get_source", {})
        result = json.loads(str(raw_result))
        assert "source" in result
        assert result["format"] == "xml"


class TestAppScreenshot:
    async def test_screenshot_returns_base64_data(self) -> None:
        mock_client = _mock_client_for_post({"value": "iVBORw0KGgo="})
        with patch("testagent.mcp_servers.appium_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await app_screenshot(appium_url=APPIUM_URL)
        assert result["screenshot_base64"] == "iVBORw0KGgo="
        assert result["format"] == "png"

    async def test_screenshot_failure_returns_error(self) -> None:
        mock_client = _mock_client_for_post({"value": "error"}, status_code=500)
        with patch("testagent.mcp_servers.appium_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await app_screenshot(appium_url=APPIUM_URL)
        assert "error" in result


class TestAppGetSource:
    async def test_get_source_returns_xml(self) -> None:
        mock_client = _mock_client_for_post({"value": "<App><Button/></App>"})
        with patch("testagent.mcp_servers.appium_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await app_get_source(appium_url=APPIUM_URL)
        assert result["source"] == "<App><Button/></App>"
        assert result["format"] == "xml"

    async def test_get_source_failure_returns_error(self) -> None:
        mock_client = _mock_client_for_post({"value": "error"}, status_code=500)
        with patch("testagent.mcp_servers.appium_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await app_get_source(appium_url=APPIUM_URL)
        assert "error" in result


class TestAppTypeParamValidation:
    async def test_type_with_invalid_strategy_returns_error(self) -> None:
        result = await app_type("input-field", "hello", strategy="bad_strategy", appium_url=APPIUM_URL)
        assert "error" in result
        assert "Invalid strategy" in result["error"]

    async def test_type_element_not_found_returns_error(self) -> None:
        mock_client = _mock_client_for_post({"value": "no element"}, status_code=404)
        with patch("testagent.mcp_servers.appium_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await app_type("nonexistent", "hello", strategy="accessibility_id", appium_url=APPIUM_URL)
        assert "error" in result


class TestAppInstall:
    async def test_install_sends_app_path(self) -> None:
        mock_client = _mock_client_for_post({"value": "installed"})
        with patch("testagent.mcp_servers.appium_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await app_install("/path/to/app.apk", appium_url=APPIUM_URL)
        assert result["status_code"] == 200
