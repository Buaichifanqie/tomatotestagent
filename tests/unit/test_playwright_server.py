from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from testagent.mcp_servers.playwright_server.server import PlaywrightMCPServer
from testagent.mcp_servers.playwright_server.tools import (
    browser_assert,
    browser_click,
    browser_get_console,
    browser_get_network,
    browser_navigate,
    browser_screenshot,
    browser_type,
)


@pytest.fixture()
def server() -> PlaywrightMCPServer:
    return PlaywrightMCPServer()


def _make_mock_page(**kwargs: object) -> MagicMock:
    page = MagicMock()
    page.url = kwargs.get("url", "about:blank")
    page.title = AsyncMock(return_value=kwargs.get("title", "Mock Page"))
    page.goto = AsyncMock(return_value=kwargs.get("response", MagicMock(status=200)))
    page.click = AsyncMock()
    page.fill = AsyncMock()
    page.type = AsyncMock()
    page.screenshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n")
    page.query_selector = AsyncMock(return_value=kwargs.get("element"))
    page.query_selector_all = AsyncMock(return_value=kwargs.get("elements", []))
    page.wait_for_selector = AsyncMock()
    page.is_enabled = AsyncMock(return_value=kwargs.get("enabled", True))
    page.evaluate = AsyncMock(return_value=True)
    return page


def _make_mock_element(**kwargs: object) -> MagicMock:
    element = MagicMock()
    element.screenshot = AsyncMock(return_value=b"\x89PNG\r\n\x1a\n")
    element.inner_text = AsyncMock(return_value=kwargs.get("text", ""))
    element.input_value = AsyncMock(return_value=kwargs.get("value", ""))
    element.get_attribute = AsyncMock(return_value=kwargs.get("attribute", ""))
    return element


class TestPlaywrightMCPServer:
    def test_server_name_is_playwright_server(self, server: PlaywrightMCPServer) -> None:
        assert server.server_name == "playwright_server"

    async def test_list_tools_returns_seven_tools(self, server: PlaywrightMCPServer) -> None:
        tools = await server.list_tools()
        assert len(tools) == 7
        tool_names = {t["name"] for t in tools}
        assert tool_names == {
            "browser_navigate",
            "browser_click",
            "browser_type",
            "browser_screenshot",
            "browser_assert",
            "browser_get_console",
            "browser_get_network",
        }

    async def test_list_tools_input_schemas_have_required_fields(self, server: PlaywrightMCPServer) -> None:
        tools = await server.list_tools()
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert "required" in tool["inputSchema"]

    async def test_list_resources_returns_three_resources(self, server: PlaywrightMCPServer) -> None:
        resources = await server.list_resources()
        assert len(resources) == 3
        uris = {r["uri"] for r in resources}
        assert uris == {"page://dom", "page://console", "page://network"}

    async def test_list_resources_have_required_fields(self, server: PlaywrightMCPServer) -> None:
        resources = await server.list_resources()
        for resource in resources:
            assert "uri" in resource
            assert "name" in resource
            assert "mimeType" in resource
            assert "description" in resource

    async def test_health_check_without_page_returns_true(self, server: PlaywrightMCPServer) -> None:
        server._page = None
        result = await server.health_check()
        assert result is True

    async def test_health_check_with_mock_page_returns_true(self, server: PlaywrightMCPServer) -> None:
        mock_page = _make_mock_page()
        server._page = mock_page
        result = await server.health_check()
        assert result is True
        mock_page.evaluate.assert_called_once_with("() => true")

    async def test_health_check_with_broken_page_returns_false(self, server: PlaywrightMCPServer) -> None:
        mock_page = _make_mock_page()
        mock_page.evaluate = AsyncMock(side_effect=Exception("Browser crashed"))
        server._page = mock_page
        result = await server.health_check()
        assert result is False

    async def test_call_tool_dispatches_browser_navigate(self, server: PlaywrightMCPServer) -> None:
        mock_page = _make_mock_page(url="https://example.com", title="Example")
        mock_response = MagicMock(status=200)
        mock_page.goto = AsyncMock(return_value=mock_response)
        server._page = mock_page

        raw_result = await server.call_tool("browser_navigate", {"url": "https://example.com"})
        result = json.loads(str(raw_result))
        assert result["url"] == "https://example.com"
        assert result["title"] == "Example"
        assert result["status_code"] == 200

    async def test_call_tool_dispatches_browser_click(self, server: PlaywrightMCPServer) -> None:
        mock_page = _make_mock_page()
        server._page = mock_page

        raw_result = await server.call_tool("browser_click", {"selector": "#btn"})
        result = json.loads(str(raw_result))
        assert result["clicked"] is True
        assert result["selector"] == "#btn"
        mock_page.click.assert_called_once_with("#btn", button="left")

    async def test_call_tool_unknown_tool_returns_error(self, server: PlaywrightMCPServer) -> None:
        raw_result = await server.call_tool("unknown_tool", {})
        result = json.loads(str(raw_result))
        assert "error" in result
        assert "Unknown tool" in result["error"]

    async def test_call_tool_exception_returns_error(self, server: PlaywrightMCPServer) -> None:
        raw_result = await server.call_tool("browser_screenshot", {})
        result = json.loads(str(raw_result))
        assert "error" in result


class TestBrowserNavigate:
    async def test_navigate_without_page_returns_error(self) -> None:
        result = await browser_navigate("https://example.com")
        assert "error" in result
        assert result["error"] == "Browser not initialized"

    async def test_navigate_returns_url_title_and_status(self) -> None:
        mock_page = _make_mock_page(url="https://example.com", title="Example")
        mock_response = MagicMock(status=200)
        mock_page.goto = AsyncMock(return_value=mock_response)

        result = await browser_navigate("https://example.com", page=mock_page)
        assert result["url"] == "https://example.com"
        assert result["title"] == "Example"
        assert result["status_code"] == 200
        mock_page.goto.assert_called_once_with("https://example.com", wait_until="load")

    async def test_navigate_with_custom_wait_until(self) -> None:
        mock_page = _make_mock_page(url="https://example.com", title="Example")
        mock_response = MagicMock(status=200)
        mock_page.goto = AsyncMock(return_value=mock_response)

        result = await browser_navigate("https://example.com", wait_until="networkidle", page=mock_page)
        assert result["status_code"] == 200
        mock_page.goto.assert_called_once_with("https://example.com", wait_until="networkidle")


class TestBrowserClick:
    async def test_click_without_page_returns_error(self) -> None:
        result = await browser_click("#btn")
        assert "error" in result
        assert result["error"] == "Browser not initialized"

    async def test_click_calls_page_click(self) -> None:
        mock_page = _make_mock_page()

        result = await browser_click("#submit", page=mock_page)
        assert result["clicked"] is True
        assert result["selector"] == "#submit"
        mock_page.click.assert_called_once_with("#submit", button="left")

    async def test_click_with_right_button(self) -> None:
        mock_page = _make_mock_page()

        result = await browser_click("#menu", button="right", page=mock_page)
        assert result["clicked"] is True
        mock_page.click.assert_called_once_with("#menu", button="right")


class TestBrowserType:
    async def test_type_without_page_returns_error(self) -> None:
        result = await browser_type("#input", "hello")
        assert "error" in result
        assert result["error"] == "Browser not initialized"

    async def test_type_calls_fill_and_type(self) -> None:
        mock_page = _make_mock_page()

        result = await browser_type("#input", "hello", page=mock_page)
        assert result["typed"] is True
        assert result["selector"] == "#input"
        assert result["text"] == "hello"
        mock_page.fill.assert_called_once_with("#input", "")
        mock_page.type.assert_called_once_with("#input", "hello")

    async def test_type_without_clear(self) -> None:
        mock_page = _make_mock_page()

        result = await browser_type("#input", "hello", clear=False, page=mock_page)
        assert result["typed"] is True
        mock_page.fill.assert_not_called()
        mock_page.type.assert_called_once_with("#input", "hello")


class TestBrowserScreenshot:
    async def test_screenshot_without_page_returns_error(self) -> None:
        result = await browser_screenshot()
        assert "error" in result
        assert result["error"] == "Browser not initialized"

    async def test_screenshot_full_page(self) -> None:
        mock_page = _make_mock_page()

        result = await browser_screenshot(full_page=True, page=mock_page)
        assert "screenshot_base64" in result
        assert result["format"] == "png"
        assert result["full_page"] is True
        assert result["screenshot_base64"].startswith("iVBOR")
        mock_page.screenshot.assert_called_once_with(full_page=True)

    async def test_screenshot_element(self) -> None:
        mock_element = _make_mock_element()
        mock_page = _make_mock_page(element=mock_element)

        result = await browser_screenshot(selector="#logo", page=mock_page)
        assert "screenshot_base64" in result
        assert result["format"] == "png"
        assert result["full_page"] is False
        mock_page.query_selector.assert_called_once_with("#logo")
        mock_element.screenshot.assert_called_once()

    async def test_screenshot_element_not_found(self) -> None:
        mock_page = _make_mock_page(element=None)

        result = await browser_screenshot(selector="#missing", page=mock_page)
        assert "error" in result
        assert "Element not found" in str(result["error"])


class TestBrowserAssert:
    async def test_assert_without_page_returns_error(self) -> None:
        result = await browser_assert("#el", "visible")
        assert "error" in result
        assert result["error"] == "Browser not initialized"

    async def test_assert_unknown_type_returns_error(self) -> None:
        mock_page = _make_mock_page()
        result = await browser_assert("#el", "invalid", page=mock_page)
        assert "error" in result
        assert "Unknown assertion" in str(result["error"])

    async def test_assert_visible_passes(self) -> None:
        mock_page = _make_mock_page()
        result = await browser_assert("#el", "visible", page=mock_page)
        assert result["passed"] is True
        mock_page.wait_for_selector.assert_called_once_with("#el", state="visible", timeout=5000)

    async def test_assert_visible_fails(self) -> None:
        mock_page = _make_mock_page()
        mock_page.wait_for_selector = AsyncMock(side_effect=Exception("Timeout"))
        result = await browser_assert("#el", "visible", page=mock_page)
        assert result["passed"] is False

    async def test_assert_hidden_passes(self) -> None:
        mock_page = _make_mock_page()
        result = await browser_assert("#el", "hidden", page=mock_page)
        assert result["passed"] is True
        mock_page.wait_for_selector.assert_called_once_with("#el", state="hidden", timeout=5000)

    async def test_assert_exists_element_found(self) -> None:
        mock_element = _make_mock_element()
        mock_page = _make_mock_page(element=mock_element)
        result = await browser_assert("#el", "exists", page=mock_page)
        assert result["passed"] is True

    async def test_assert_exists_element_not_found(self) -> None:
        mock_page = _make_mock_page(element=None)
        result = await browser_assert("#missing", "exists", page=mock_page)
        assert result["passed"] is False

    async def test_assert_text_matches(self) -> None:
        mock_element = _make_mock_element(text="Hello World")
        mock_page = _make_mock_page(element=mock_element)
        result = await browser_assert("#el", "text", expected="Hello World", page=mock_page)
        assert result["passed"] is True
        assert result["actual"] == "Hello World"

    async def test_assert_text_mismatch(self) -> None:
        mock_element = _make_mock_element(text="Goodbye")
        mock_page = _make_mock_page(element=mock_element)
        result = await browser_assert("#el", "text", expected="Hello", page=mock_page)
        assert result["passed"] is False
        assert result["actual"] == "Goodbye"

    async def test_assert_text_element_not_found(self) -> None:
        mock_page = _make_mock_page(element=None)
        result = await browser_assert("#missing", "text", expected="Hello", page=mock_page)
        assert result["passed"] is False

    async def test_assert_value_matches(self) -> None:
        mock_element = _make_mock_element(value="test@example.com")
        mock_page = _make_mock_page(element=mock_element)
        result = await browser_assert("#email", "value", expected="test@example.com", page=mock_page)
        assert result["passed"] is True
        assert result["actual"] == "test@example.com"

    async def test_assert_enabled(self) -> None:
        mock_page = _make_mock_page(enabled=True)
        result = await browser_assert("#btn", "enabled", page=mock_page)
        assert result["passed"] is True

    async def test_assert_disabled(self) -> None:
        mock_page = _make_mock_page(enabled=False)
        result = await browser_assert("#btn", "disabled", page=mock_page)
        assert result["passed"] is True

    async def test_assert_enabled_fails_when_disabled(self) -> None:
        mock_page = _make_mock_page(enabled=False)
        result = await browser_assert("#btn", "enabled", page=mock_page)
        assert result["passed"] is False

    async def test_assert_count_matches(self) -> None:
        mock_elements = [MagicMock(), MagicMock(), MagicMock()]
        mock_page = _make_mock_page(elements=mock_elements)
        result = await browser_assert(".item", "count", expected="3", page=mock_page)
        assert result["passed"] is True
        assert result["actual"] == 3

    async def test_assert_count_mismatch(self) -> None:
        mock_elements = [MagicMock()]
        mock_page = _make_mock_page(elements=mock_elements)
        result = await browser_assert(".item", "count", expected="5", page=mock_page)
        assert result["passed"] is False
        assert result["actual"] == 1
        assert result["expected"] == 5

    async def test_assert_url_matches(self) -> None:
        mock_page = _make_mock_page(url="https://example.com/dashboard")
        result = await browser_assert(".el", "url", expected="dashboard", page=mock_page)
        assert result["passed"] is True
        assert result["actual"] == "https://example.com/dashboard"

    async def test_assert_url_without_expected(self) -> None:
        mock_page = _make_mock_page(url="https://example.com/home")
        result = await browser_assert(".el", "url", page=mock_page)
        assert result["passed"] is True
        assert result["actual"] == "https://example.com/home"

    async def test_assert_title_matches(self) -> None:
        mock_page = _make_mock_page(title="Welcome to My App")
        result = await browser_assert(".el", "title", expected="Welcome", page=mock_page)
        assert result["passed"] is True
        assert result["actual"] == "Welcome to My App"

    async def test_assert_attribute_matches(self) -> None:
        mock_element = _make_mock_element(attribute="btn-primary")
        mock_page = _make_mock_page(element=mock_element)
        result = await browser_assert("#btn", "attribute", expected="class=btn-primary", page=mock_page)
        assert result["passed"] is True
        assert result["actual"] == "btn-primary"
        assert result["expected"] == "btn-primary"

    async def test_assert_attribute_without_expected(self) -> None:
        mock_page = _make_mock_page(element=_make_mock_element())
        result = await browser_assert("#btn", "attribute", page=mock_page)
        assert "error" in result
        assert "requires expected parameter" in str(result["error"])


class TestBrowserGetConsole:
    async def test_get_console_empty(self) -> None:
        result = await browser_get_console()
        assert result["console_messages"] == []

    async def test_get_console_with_messages(self) -> None:
        messages: list[dict[str, object]] = [
            {"type": "log", "text": "hello", "location": "app.js:1", "timestamp": "0:00"},
            {"type": "error", "text": "boom", "location": "app.js:5", "timestamp": "0:01"},
        ]
        result = await browser_get_console(console_messages=messages)
        assert len(result["console_messages"]) == 2
        assert result["console_messages"][0]["type"] == "log"
        assert result["console_messages"][1]["type"] == "error"

    async def test_get_console_returns_copy(self) -> None:
        messages: list[dict[str, object]] = [{"type": "log", "text": "original", "location": "", "timestamp": ""}]
        result = await browser_get_console(console_messages=messages)
        result["console_messages"].append({"type": "warning", "text": "added", "location": "", "timestamp": ""})
        assert len(messages) == 1


class TestBrowserGetNetwork:
    async def test_get_network_empty(self) -> None:
        result = await browser_get_network()
        assert result["requests"] == []

    async def test_get_network_with_requests(self) -> None:
        requests: list[dict[str, object]] = [
            {"url": "https://api.example.com/users", "method": "GET", "status_code": 200},
            {"url": "https://api.example.com/items", "method": "POST", "status_code": 201},
        ]
        result = await browser_get_network(network_requests=requests)
        assert len(result["requests"]) == 2
        assert result["requests"][0]["method"] == "GET"
        assert result["requests"][1]["method"] == "POST"

    async def test_get_network_filter_by_url_pattern(self) -> None:
        requests: list[dict[str, object]] = [
            {"url": "https://api.example.com/users", "method": "GET", "status_code": 200},
            {"url": "https://api.example.com/items", "method": "GET", "status_code": 200},
            {"url": "https://cdn.example.com/logo.png", "method": "GET", "status_code": 200},
        ]
        result = await browser_get_network(url_pattern=r"api\.example\.com", network_requests=requests)
        assert len(result["requests"]) == 2

    async def test_get_network_invalid_regex_returns_error(self) -> None:
        requests: list[dict[str, object]] = [{"url": "https://example.com", "method": "GET", "status_code": 200}]
        result = await browser_get_network(url_pattern="[invalid", network_requests=requests)
        assert "error" in result
        assert "Invalid regex pattern" in str(result["error"])

    async def test_get_network_no_matches_returns_empty(self) -> None:
        requests: list[dict[str, object]] = [
            {"url": "https://api.example.com/users", "method": "GET", "status_code": 200},
        ]
        result = await browser_get_network(url_pattern=r"cdn\.example\.com", network_requests=requests)
        assert result["requests"] == []

    async def test_get_network_returns_copy(self) -> None:
        requests: list[dict[str, object]] = [{"url": "https://example.com", "method": "GET", "status_code": 200}]
        result = await browser_get_network(network_requests=requests)
        result["requests"].append({"url": "https://other.com", "method": "POST", "status_code": 201})
        assert len(requests) == 1
