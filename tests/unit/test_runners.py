from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.harness.runners import (
    AppiumRunner,
    BaseRunner,
    HTTPRunner,
    IRunner,
    PlaywrightRunner,
    RunnerError,
    RunnerFactory,
    UnknownTaskTypeError,
)
from testagent.models.result import TestResult


class TestRunnerFactory:
    def test_get_http_runner(self) -> None:
        runner = RunnerFactory.get_runner("api_test")
        assert isinstance(runner, HTTPRunner)
        assert isinstance(runner, IRunner)

    def test_get_playwright_runner(self) -> None:
        runner = RunnerFactory.get_runner("web_test")
        assert isinstance(runner, PlaywrightRunner)
        assert isinstance(runner, IRunner)

    def test_get_appium_runner(self) -> None:
        runner = RunnerFactory.get_runner("app_test")
        assert isinstance(runner, AppiumRunner)
        assert isinstance(runner, IRunner)

    def test_get_unknown_task_type_raises_error(self) -> None:
        with pytest.raises(UnknownTaskTypeError) as excinfo:
            RunnerFactory.get_runner("invalid_type")
        assert "invalid_type" in str(excinfo.value)

    def test_get_unknown_task_type_has_correct_code(self) -> None:
        with pytest.raises(UnknownTaskTypeError) as excinfo:
            RunnerFactory.get_runner("invalid_type")
        assert excinfo.value.task_type == "invalid_type"
        assert excinfo.value.code == "UNKNOWN_TASK_TYPE"

    def test_runner_factory_returns_new_instance_each_time(self) -> None:
        runner1 = RunnerFactory.get_runner("api_test")
        runner2 = RunnerFactory.get_runner("api_test")
        assert runner1 is not runner2

    def test_register_custom_runner(self) -> None:
        class FakeRunner(BaseRunner):
            runner_type = "custom_test"

        RunnerFactory.register("custom_test", FakeRunner)
        runner = RunnerFactory.get_runner("custom_test")
        assert isinstance(runner, FakeRunner)
        assert isinstance(runner, IRunner)


class TestIRunnerProtocol:
    def test_http_runner_complies_with_protocol(self) -> None:
        assert isinstance(HTTPRunner(), IRunner)

    def test_playwright_runner_complies_with_protocol(self) -> None:
        assert isinstance(PlaywrightRunner(), IRunner)

    def test_appium_runner_complies_with_protocol(self) -> None:
        assert isinstance(AppiumRunner(), IRunner)

    def test_base_runner_complies_with_protocol(self) -> None:
        assert isinstance(BaseRunner(), IRunner)

    def test_protocol_method_signatures_match(self) -> None:
        import inspect

        for cls in [HTTPRunner, PlaywrightRunner, AppiumRunner]:
            for method_name in ["setup", "execute", "teardown", "collect_results"]:
                assert hasattr(cls, method_name), f"{cls.__name__} missing {method_name}"
                method = getattr(cls, method_name)
                assert inspect.iscoroutinefunction(method), f"{cls.__name__}.{method_name} must be async"


class TestBaseRunner:
    def test_validate_config_passes_with_all_keys(self) -> None:
        runner = BaseRunner()
        runner._validate_config({"key1": "v1", "key2": "v2"}, ["key1", "key2"])

    def test_validate_config_raises_on_missing_keys(self) -> None:
        runner = BaseRunner()
        with pytest.raises(RunnerError) as excinfo:
            runner._validate_config({"key1": "v1"}, ["key1", "key2"])
        assert "key2" in str(excinfo.value)
        assert excinfo.value.code == "MISSING_CONFIG"

    def test_make_result_defaults(self) -> None:
        runner = BaseRunner()
        result = runner._make_result("passed")
        assert isinstance(result, TestResult)
        assert result.status == "passed"
        assert result.assertion_results == {}
        assert result.artifacts == {}
        assert result.logs == ""

    def test_make_result_with_all_fields(self) -> None:
        runner = BaseRunner()
        result = runner._make_result(
            "failed",
            task_id="task-123",
            duration_ms=1500.0,
            assertion_results={"status_code": {"passed": False}},
            logs="Error occurred",
            artifacts={"trace": "abc"},
        )
        assert result.status == "failed"
        assert result.task_id == "task-123"
        assert result.duration_ms == 1500.0
        assert result.assertion_results == {"status_code": {"passed": False}}
        assert result.logs == "Error occurred"
        assert result.artifacts == {"trace": "abc"}

    @pytest.mark.asyncio
    async def test_base_runner_methods_raise_not_implemented(self) -> None:
        runner = BaseRunner()
        with pytest.raises(NotImplementedError):
            await runner.execute("test")
        with pytest.raises(NotImplementedError):
            await runner.teardown()
        with pytest.raises(NotImplementedError):
            await runner.collect_results()

    @pytest.mark.asyncio
    async def test_base_runner_setup_stores_sandbox_ref(self) -> None:
        runner = BaseRunner()
        await runner.setup({"url": "http://test.com"})
        assert runner._sandbox is None
        assert runner._sandbox_id is None


class TestHTTPRunner:
    def test_runner_type(self) -> None:
        assert HTTPRunner.runner_type == "api_test"

    @pytest.mark.asyncio
    async def test_setup_creates_client(self) -> None:
        runner = HTTPRunner()
        await runner.setup({"base_url": "http://test.com"})
        assert runner._client is not None
        assert runner._base_url == "http://test.com"
        await runner.teardown()

    @pytest.mark.asyncio
    async def test_setup_requires_base_url(self) -> None:
        runner = HTTPRunner()
        with pytest.raises(RunnerError) as excinfo:
            await runner.setup({})
        assert "base_url" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_execute_without_setup_raises_error(self) -> None:
        runner = HTTPRunner()
        with pytest.raises(RunnerError) as excinfo:
            await runner.execute('{"method": "GET", "path": "/"}')
        assert "RUNNER_NOT_SETUP" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_execute_successful_get_request(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"key": "value"}

        runner = HTTPRunner()
        await runner.setup({"base_url": "http://test.com"})
        runner._client = AsyncMock(spec=runner._client.__class__)
        runner._client.request = AsyncMock(return_value=mock_response)
        runner._client.is_closed = False

        result = await runner.execute(
            json.dumps(
                {
                    "method": "GET",
                    "path": "/api/test",
                    "assertions": {"status_code": 200},
                }
            )
        )

        assert result.status == "passed"
        assert result.assertion_results["status_code"]["passed"] is True  # type: ignore[index]
        await runner.teardown()

    @pytest.mark.asyncio
    async def test_execute_with_failed_assertion(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"error": "not found"}

        runner = HTTPRunner()
        await runner.setup({"base_url": "http://test.com"})
        runner._client = AsyncMock(spec=runner._client.__class__)
        runner._client.request = AsyncMock(return_value=mock_response)
        runner._client.is_closed = False

        result = await runner.execute(
            json.dumps(
                {
                    "method": "GET",
                    "path": "/api/not-found",
                    "assertions": {"status_code": 200},
                }
            )
        )

        assert result.status == "failed"
        assert result.assertion_results["status_code"]["passed"] is False  # type: ignore[index]
        assert result.assertion_results["status_code"]["expected"] == 200  # type: ignore[index]
        assert result.assertion_results["status_code"]["actual"] == 404  # type: ignore[index]
        await runner.teardown()

    @pytest.mark.asyncio
    async def test_execute_with_timeout(self) -> None:
        runner = HTTPRunner()
        await runner.setup({"base_url": "http://test.com"})
        runner._client = AsyncMock(spec=runner._client.__class__)
        runner._client.request = AsyncMock(side_effect=httpx_timeout_exception())
        runner._client.is_closed = False

        result = await runner.execute(
            json.dumps(
                {
                    "method": "GET",
                    "path": "/api/slow",
                }
            )
        )

        assert result.status == "failed"
        assert "timed out" in (result.logs or "").lower()
        await runner.teardown()

    @pytest.mark.asyncio
    async def test_execute_with_http_error(self) -> None:
        runner = HTTPRunner()
        await runner.setup({"base_url": "http://test.com"})
        runner._client = AsyncMock(spec=runner._client.__class__)
        runner._client.request = AsyncMock(side_effect=httpx_http_error())
        runner._client.is_closed = False

        result = await runner.execute(
            json.dumps(
                {
                    "method": "GET",
                    "path": "/api/error",
                }
            )
        )

        assert result.status == "error"
        await runner.teardown()

    @pytest.mark.asyncio
    async def test_execute_with_json_path_assertion(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"user": {"name": "Alice", "age": 30}}

        runner = HTTPRunner()
        await runner.setup({"base_url": "http://test.com"})
        runner._client = AsyncMock(spec=runner._client.__class__)
        runner._client.request = AsyncMock(return_value=mock_response)
        runner._client.is_closed = False

        result = await runner.execute(
            json.dumps(
                {
                    "method": "GET",
                    "path": "/api/user",
                    "assertions": {"json_path": {"user.name": "Alice", "user.age": 30}},
                }
            )
        )

        assert result.status == "passed"
        assert result.assertion_results["json_path"]["user.name"]["passed"] is True  # type: ignore[index]
        assert result.assertion_results["json_path"]["user.age"]["passed"] is True  # type: ignore[index]
        await runner.teardown()

    @pytest.mark.asyncio
    async def test_teardown_closes_client(self) -> None:
        runner = HTTPRunner()
        await runner.setup({"base_url": "http://test.com"})
        assert runner._client is not None
        await runner.teardown()
        assert runner._client is None

    @pytest.mark.asyncio
    async def test_collect_results_returns_summary(self) -> None:
        runner = HTTPRunner()
        await runner.setup({"base_url": "http://test.com"})

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"ok": True}

        runner._client = AsyncMock(spec=runner._client.__class__)
        runner._client.request = AsyncMock(return_value=mock_response)
        runner._client.is_closed = False

        await runner.execute(json.dumps({"method": "GET", "path": "/api/test"}))
        result = await runner.collect_results()

        assert result.status == "passed"
        assert result.artifacts is not None
        assert result.artifacts["total_requests"] == 1  # type: ignore[index]
        await runner.teardown()

    @pytest.mark.asyncio
    async def test_invalid_script_raises_error(self) -> None:
        runner = HTTPRunner()
        await runner.setup({"base_url": "http://test.com"})
        runner._client = AsyncMock()
        runner._client.is_closed = False

        with pytest.raises(RunnerError) as excinfo:
            await runner.execute("not valid json")
        assert "INVALID_SCRIPT" in excinfo.value.code
        await runner.teardown()

    @pytest.mark.asyncio
    async def test_invalid_http_method_raises_error(self) -> None:
        runner = HTTPRunner()
        await runner.setup({"base_url": "http://test.com"})
        runner._client = AsyncMock()
        runner._client.is_closed = False

        with pytest.raises(RunnerError) as excinfo:
            await runner.execute(json.dumps({"method": "INVALID", "path": "/"}))
        assert "INVALID_METHOD" in excinfo.value.code
        await runner.teardown()

    @pytest.mark.asyncio
    async def test_status_code_in_assertion_passes(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {}

        runner = HTTPRunner()
        await runner.setup({"base_url": "http://test.com"})
        runner._client = AsyncMock(spec=runner._client.__class__)
        runner._client.request = AsyncMock(return_value=mock_response)
        runner._client.is_closed = False

        result = await runner.execute(
            json.dumps(
                {
                    "method": "GET",
                    "path": "/",
                    "assertions": {"status_code_in": [200, 201]},
                }
            )
        )

        assert result.status == "passed"
        assert result.assertion_results["status_code_in"]["passed"] is True  # type: ignore[index]
        await runner.teardown()


class TestPlaywrightRunner:
    def test_runner_type(self) -> None:
        assert PlaywrightRunner.runner_type == "web_test"

    @pytest.mark.asyncio
    async def test_setup_requires_browser_type(self) -> None:
        runner = PlaywrightRunner()
        with pytest.raises(RunnerError) as excinfo:
            await runner.setup({})
        assert "browser_type" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_setup_invalid_browser_type(self) -> None:
        runner = PlaywrightRunner()
        with pytest.raises(RunnerError) as excinfo:
            await runner.setup({"browser_type": "safari"})
        assert "safari" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_setup_playwright_not_installed(self) -> None:
        with patch.dict("sys.modules", {"playwright.async_api": None}):
            runner = PlaywrightRunner()
            with pytest.raises(RunnerError) as excinfo:
                await runner.setup({"browser_type": "chromium"})
            assert "PLAYWRIGHT_NOT_INSTALLED" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_execute_without_setup_raises_error(self) -> None:
        runner = PlaywrightRunner()
        with pytest.raises(RunnerError) as excinfo:
            await runner.execute('{"actions": []}')
        assert "RUNNER_NOT_SETUP" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_execute_invalid_json_raises_error(self) -> None:
        runner = PlaywrightRunner()
        runner._page = MagicMock()
        with pytest.raises(RunnerError) as excinfo:
            await runner.execute("not json")
        assert "INVALID_SCRIPT" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_execute_missing_actions_key(self) -> None:
        runner = PlaywrightRunner()
        runner._page = MagicMock()
        with pytest.raises(RunnerError) as excinfo:
            await runner.execute('{"not_actions": "value"}')
        assert "INVALID_SCRIPT" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_execute_unknown_action(self) -> None:
        runner = PlaywrightRunner()
        runner._page = MagicMock()
        result = await runner.execute(json.dumps({"actions": [{"action": "fly"}]}))
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_execute_empty_actions(self) -> None:
        runner = PlaywrightRunner()
        runner._page = MagicMock()
        result = await runner.execute(json.dumps({"actions": []}))
        assert result.status == "passed"

    @pytest.mark.asyncio
    async def test_execute_navigate_action(self) -> None:
        mock_page = AsyncMock()
        mock_page.url = "http://test.com"

        runner = PlaywrightRunner()
        runner._page = mock_page

        result = await runner.execute(
            json.dumps(
                {
                    "actions": [{"action": "navigate", "url": "http://test.com"}],
                }
            )
        )

        mock_page.goto.assert_awaited_once_with("http://test.com", wait_until="load", timeout=30000)
        assert result.status == "passed"

    @pytest.mark.asyncio
    async def test_execute_all_basic_interaction_actions(self) -> None:
        mock_page = AsyncMock()
        mock_page.url = "http://test.com"

        runner = PlaywrightRunner()
        runner._page = mock_page

        result = await runner.execute(
            json.dumps(
                {
                    "actions": [
                        {"action": "click", "selector": "#btn"},
                        {"action": "fill", "selector": "#input", "value": "hello"},
                        {"action": "type", "selector": "#field", "value": "world", "delay": 10},
                        {"action": "select", "selector": "#dropdown", "value": "option1"},
                        {"action": "check", "selector": "#checkbox"},
                        {"action": "uncheck", "selector": "#checkbox"},
                        {"action": "hover", "selector": "#menu"},
                    ],
                }
            )
        )

        mock_page.click.assert_awaited_once_with("#btn", timeout=30000)
        mock_page.fill.assert_awaited_once_with("#input", "hello", timeout=30000)
        mock_page.type.assert_awaited_once_with("#field", "world", delay=10, timeout=30000)
        mock_page.select_option.assert_awaited_once_with("#dropdown", "option1", timeout=30000)
        mock_page.check.assert_awaited_once_with("#checkbox", timeout=30000)
        mock_page.uncheck.assert_awaited_once_with("#checkbox", timeout=30000)
        mock_page.hover.assert_awaited_once_with("#menu", timeout=30000)
        assert result.status == "passed"

    @pytest.mark.asyncio
    async def test_execute_wait_actions(self) -> None:
        mock_element = AsyncMock()
        mock_page = AsyncMock()
        mock_page.url = "http://test.com"
        mock_page.wait_for_selector = AsyncMock(return_value=mock_element)
        mock_page.wait_for_load_state = AsyncMock()

        runner = PlaywrightRunner()
        runner._page = mock_page

        result = await runner.execute(
            json.dumps(
                {
                    "actions": [
                        {"action": "wait_for_selector", "selector": ".loaded", "state": "visible"},
                    ],
                }
            )
        )

        mock_page.wait_for_selector.assert_awaited_once_with(".loaded", state="visible", timeout=30000)
        assert result.status == "passed"

    @pytest.mark.asyncio
    async def test_execute_assertion_actions(self) -> None:
        mock_page = AsyncMock()
        mock_page.url = "http://test.com/expected"
        mock_page.text_content = AsyncMock(return_value="expected text")
        mock_page.title = AsyncMock(return_value="Test Page")
        mock_page.is_visible = AsyncMock(return_value=True)

        runner = PlaywrightRunner()
        runner._page = mock_page

        result = await runner.execute(
            json.dumps(
                {
                    "actions": [
                        {
                            "action": "assert_text",
                            "selector": "#title",
                            "expected_text": "expected text",
                            "assertion": True,
                        },
                        {"action": "assert_url", "expected_url": "http://test.com/expected", "assertion": True},
                        {"action": "assert_title", "expected_title": "Test Page", "assertion": True},
                        {"action": "is_visible", "selector": "#header"},
                    ],
                }
            )
        )

        assert result.status == "passed"

    @pytest.mark.asyncio
    async def test_execute_failed_assertion(self) -> None:
        mock_page = AsyncMock()
        mock_page.url = "http://test.com"
        mock_page.text_content = AsyncMock(return_value="wrong text")

        runner = PlaywrightRunner()
        runner._page = mock_page

        result = await runner.execute(
            json.dumps(
                {
                    "actions": [
                        {
                            "action": "assert_text",
                            "selector": "#title",
                            "expected_text": "expected text",
                            "assertion": True,
                            "assertion_label": "title_check",
                        }
                    ],
                }
            )
        )

        assert result.status == "failed"
        assert result.assertion_results["title_check"]["passed"] is False  # type: ignore[index]
        assert result.assertion_results["title_check"]["actual"] == "wrong text"  # type: ignore[index]
        assert result.assertion_results["title_check"]["expected"] == "expected text"  # type: ignore[index]

    @pytest.mark.asyncio
    async def test_execute_with_runtime_error_captures_screenshot(self) -> None:
        mock_page = AsyncMock()
        mock_page.click = AsyncMock(side_effect=Exception("Element not found"))
        mock_page.screenshot = AsyncMock(return_value=b"fake_png_bytes")

        runner = PlaywrightRunner()
        runner._page = mock_page

        result = await runner.execute(
            json.dumps(
                {
                    "actions": [{"action": "click", "selector": "#nonexistent"}],
                }
            )
        )

        assert result.status == "error"
        mock_page.screenshot.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_teardown_closes_resources(self) -> None:
        mock_page = AsyncMock()
        mock_context = AsyncMock()
        mock_browser = AsyncMock()
        mock_playwright = MagicMock()
        mock_playwright.stop = AsyncMock()

        runner = PlaywrightRunner()
        runner._page = mock_page
        runner._context = mock_context
        runner._browser = mock_browser
        runner._playwright = mock_playwright

        await runner.teardown()

        mock_context.close.assert_awaited_once()
        mock_browser.close.assert_awaited_once()
        mock_playwright.stop.assert_awaited_once()
        assert runner._page is None
        assert runner._context is None
        assert runner._browser is None
        assert runner._playwright is None

    @pytest.mark.asyncio
    async def test_teardown_safe_when_not_initialized(self) -> None:
        runner = PlaywrightRunner()
        await runner.teardown()

    @pytest.mark.asyncio
    async def test_collect_results_returns_summary(self) -> None:
        mock_page = AsyncMock()
        mock_page.url = "http://test.com"

        runner = PlaywrightRunner()
        runner._page = mock_page

        await runner.execute(
            json.dumps(
                {
                    "actions": [{"action": "navigate", "url": "http://test.com"}],
                }
            )
        )

        result = await runner.collect_results()
        assert result.status == "passed"
        assert result.artifacts is not None
        assert result.artifacts["total_actions"] == 1


def httpx_timeout_exception() -> Exception:
    import httpx

    return httpx.TimeoutException("Request timed out")


def httpx_http_error() -> Exception:
    import httpx

    return httpx.HTTPStatusError("502 Bad Gateway", request=MagicMock(), response=MagicMock())
