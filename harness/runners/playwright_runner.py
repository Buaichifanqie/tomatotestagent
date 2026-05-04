from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from testagent.common.logging import get_logger
from testagent.harness.runners.base import BaseRunner, RunnerError

if TYPE_CHECKING:
    from testagent.models.result import TestResult

logger = get_logger(__name__)

BROWSER_TYPES = frozenset({"chromium", "firefox", "webkit"})

PLAYWRIGHT_ACTIONS: frozenset[str] = frozenset(
    {
        "navigate",
        "click",
        "fill",
        "type",
        "select",
        "check",
        "uncheck",
        "hover",
        "wait_for_selector",
        "wait_for_navigation",
        "screenshot",
        "evaluate",
        "get_text",
        "get_attribute",
        "is_visible",
        "assert_text",
        "assert_visible",
        "assert_url",
        "assert_title",
    }
)


class PlaywrightRunner(BaseRunner):
    runner_type = "web_test"

    def __init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._headless: bool = True
        self._action_log: list[dict[str, object]] = []
        self._screenshots: list[str] = []

    async def setup(self, config: dict[str, object]) -> None:
        self._validate_config(config, ["browser_type"])
        browser_type = config["browser_type"]
        if not isinstance(browser_type, str) or browser_type not in BROWSER_TYPES:
            raise RunnerError(
                f"Unsupported browser type: {browser_type}. Must be one of {BROWSER_TYPES}",
                code="INVALID_BROWSER_TYPE",
            )

        headless_val = config.get("headless", True)
        self._headless = headless_val if isinstance(headless_val, bool) else True
        viewport = config.get("viewport", {"width": 1280, "height": 720})
        base_url = config.get("base_url", "")
        locale = config.get("locale", "en-US")
        timezone = config.get("timezone", "UTC")

        try:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            browser_launcher = getattr(self._playwright, browser_type)
            self._browser = await browser_launcher.launch(headless=self._headless)

            self._context = await self._browser.new_context(
                viewport=viewport,
                base_url=base_url,
                locale=locale,
                timezone_id=timezone,
            )
            self._page = await self._context.new_page()

            logger.info(
                "PlaywrightRunner setup",
                extra={
                    "browser_type": browser_type,
                    "headless": self._headless,
                    "viewport": viewport,
                },
            )
        except ImportError as err:
            raise RunnerError(
                "playwright library not installed. Run: pip install playwright && playwright install",
                code="PLAYWRIGHT_NOT_INSTALLED",
            ) from err

    async def execute(self, test_script: str) -> TestResult:
        if self._page is None:
            raise RunnerError("Runner not setup, call setup() first", code="RUNNER_NOT_SETUP")

        script = self._parse_script(test_script)
        actions = script.get("actions", [])
        assertion_results: dict[str, object] = {}

        start_ms = self._now_ms()

        try:
            for i, action in enumerate(actions):
                action_result = await self._execute_action(action, i)
                if "assertion" in action:
                    assertion_results.update(action_result)

            duration_ms = self._now_ms() - start_ms
            all_passed = (
                all(v.get("passed", False) for v in assertion_results.values() if isinstance(v, dict))
                if assertion_results
                else True
            )

            return self._make_result(
                status="passed" if all_passed else "failed",
                duration_ms=round(duration_ms, 2),
                assertion_results=assertion_results or {"executed": {"passed": True, "info": "No assertions defined"}},
                logs=json.dumps(self._action_log, ensure_ascii=False),
            )

        except Exception as e:
            duration_ms = self._now_ms() - start_ms
            screenshot_url = await self._capture_screenshot()
            return self._make_result(
                status="failed" if assertion_results else "error",
                duration_ms=round(duration_ms, 2),
                assertion_results=assertion_results or {"error": str(e)},
                logs=f"Execution error: {e}",
                artifacts={"screenshot": screenshot_url} if screenshot_url else None,
            )

    async def teardown(self) -> None:
        try:
            if self._page is not None:
                await self._page.close()
                self._page = None
            if self._context is not None:
                await self._context.close()
                self._context = None
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None
        except Exception as e:
            logger.warning("Error during PlaywrightRunner teardown", extra={"error": str(e)})
        logger.info("PlaywrightRunner teardown complete")

    async def collect_results(self) -> TestResult:
        combined_logs = json.dumps(self._action_log, ensure_ascii=False) if self._action_log else ""
        return self._make_result(
            status="passed",
            logs=combined_logs,
            artifacts={
                "total_actions": len(self._action_log),
                "screenshots": self._screenshots,
            },
        )

    def _parse_script(self, test_script: str) -> dict[str, Any]:
        try:
            script = json.loads(test_script)
            if not isinstance(script, dict):
                raise RunnerError("Test script must be a JSON object", code="INVALID_SCRIPT")
            if "actions" not in script:
                raise RunnerError("Test script must contain 'actions' array", code="INVALID_SCRIPT")
            if not isinstance(script["actions"], list):
                raise RunnerError("'actions' must be an array", code="INVALID_SCRIPT")
            return script
        except json.JSONDecodeError as e:
            raise RunnerError(f"Invalid JSON test script: {e}", code="INVALID_SCRIPT") from e

    async def _execute_action(self, action: dict[str, Any], index: int) -> dict[str, object]:
        action_type = action.get("action", "")
        if action_type not in PLAYWRIGHT_ACTIONS:
            raise RunnerError(
                f"Unknown action at index {index}: {action_type}",
                code="UNKNOWN_ACTION",
            )

        method_name = f"_action_{action_type}"
        method = getattr(self, method_name, None)
        if method is None:
            raise RunnerError(
                f"Action not implemented: {action_type}",
                code="ACTION_NOT_IMPLEMENTED",
            )

        selector = action.get("selector", "")
        value = action.get("value")
        timeout = action.get("timeout", 30000)

        result: dict[str, object] = {}
        assertion_result: dict[str, object] = {}

        if action.get("assertion"):
            assertion_result = await method(selector, value, timeout, action)
            result[action.get("assertion_label", f"assertion_{index}")] = assertion_result
        else:
            await method(selector, value, timeout, action)

        self._action_log.append(
            {
                "action": action_type,
                "selector": selector,
                "index": index,
            }
        )

        return result

    async def _capture_screenshot(self) -> str | None:
        if self._page is None:
            return None
        try:
            import tempfile

            screenshot_bytes = await self._page.screenshot(full_page=True)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(screenshot_bytes)
                self._screenshots.append(f.name)
                return f.name
        except Exception as e:
            logger.warning("Failed to capture screenshot", extra={"error": str(e)})
            return None

    async def _action_navigate(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._page is None:
            return {"passed": False, "error": "Page not initialized"}
        url = value or action.get("url", "")
        await self._page.goto(url, wait_until="load", timeout=timeout)
        if "assert_url" in action:
            expected_url = action["assert_url"]
            actual_url = self._page.url
            return {"passed": actual_url == expected_url, "expected": expected_url, "actual": actual_url}
        return {"passed": True}

    async def _action_click(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._page is None:
            return {"passed": False, "error": "Page not initialized"}
        await self._page.click(selector, timeout=timeout)
        return {"passed": True}

    async def _action_fill(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._page is None:
            return {"passed": False, "error": "Page not initialized"}
        if value is None:
            raise RunnerError("'fill' action requires 'value' field", code="MISSING_ACTION_VALUE")
        await self._page.fill(selector, value, timeout=timeout)
        return {"passed": True}

    async def _action_type(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._page is None:
            return {"passed": False, "error": "Page not initialized"}
        if value is None:
            raise RunnerError("'type' action requires 'value' field", code="MISSING_ACTION_VALUE")
        delay = action.get("delay", 0)
        await self._page.type(selector, value, delay=delay, timeout=timeout)
        return {"passed": True}

    async def _action_select(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._page is None:
            return {"passed": False, "error": "Page not initialized"}
        if value is None:
            raise RunnerError("'select' action requires 'value' field", code="MISSING_ACTION_VALUE")
        await self._page.select_option(selector, value, timeout=timeout)
        return {"passed": True}

    async def _action_check(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._page is None:
            return {"passed": False, "error": "Page not initialized"}
        await self._page.check(selector, timeout=timeout)
        return {"passed": True}

    async def _action_uncheck(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._page is None:
            return {"passed": False, "error": "Page not initialized"}
        await self._page.uncheck(selector, timeout=timeout)
        return {"passed": True}

    async def _action_hover(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._page is None:
            return {"passed": False, "error": "Page not initialized"}
        await self._page.hover(selector, timeout=timeout)
        return {"passed": True}

    async def _action_wait_for_selector(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._page is None:
            return {"passed": False, "error": "Page not initialized"}
        state = action.get("state", "visible")
        element = await self._page.wait_for_selector(selector, state=state, timeout=timeout)
        return {"passed": element is not None}

    async def _action_wait_for_navigation(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._page is None:
            return {"passed": False, "error": "Page not initialized"}
        url_pattern = action.get("url")
        if url_pattern:
            await self._page.wait_for_url(url_pattern, timeout=timeout)
        else:
            await self._page.wait_for_load_state("networkidle", timeout=timeout)
        return {"passed": True}

    async def _action_screenshot(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        path = await self._capture_screenshot()
        return {"passed": True, "screenshot_path": path}

    async def _action_evaluate(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._page is None:
            return {"passed": False, "error": "Page not initialized"}
        expression = value or action.get("expression", "")
        result = await self._page.evaluate(expression)
        return {"passed": True, "result": result}

    async def _action_get_text(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._page is None:
            return {"passed": False, "error": "Page not initialized"}
        text = await self._page.text_content(selector, timeout=timeout)
        if action.get("assertion"):
            expected = action.get("expected_text", "")
            return {"passed": text == expected, "actual": text, "expected": expected}
        return {"passed": True, "text": text}

    async def _action_get_attribute(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._page is None:
            return {"passed": False, "error": "Page not initialized"}
        attr_name = value or action.get("attribute", "")
        attr_value = await self._page.get_attribute(selector, attr_name, timeout=timeout)
        if action.get("assertion"):
            expected = action.get("expected_value", "")
            return {"passed": attr_value == expected, "actual": attr_value, "expected": expected}
        return {"passed": True, "value": attr_value}

    async def _action_is_visible(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._page is None:
            return {"passed": False, "error": "Page not initialized"}
        is_visible = await self._page.is_visible(selector, timeout=timeout)
        return {"passed": is_visible, "selector": selector, "visible": is_visible}

    async def _action_assert_text(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._page is None:
            return {"passed": False, "error": "Page not initialized"}
        actual = await self._page.text_content(selector, timeout=timeout)
        expected = value or action.get("expected_text", "")
        return {"passed": actual == expected, "actual": actual, "expected": expected}

    async def _action_assert_visible(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._page is None:
            return {"passed": False, "error": "Page not initialized"}
        is_visible = await self._page.is_visible(selector, timeout=timeout)
        expected = action.get("expected", True)
        return {"passed": is_visible == expected, "visible": is_visible, "expected": expected}

    async def _action_assert_url(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._page is None:
            return {"passed": False, "error": "Page not initialized"}
        actual_url = self._page.url
        expected_url = value or action.get("expected_url", "")
        return {"passed": actual_url == expected_url, "actual": actual_url, "expected": expected_url}

    async def _action_assert_title(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._page is None:
            return {"passed": False, "error": "Page not initialized"}
        actual_title = await self._page.title()
        expected_title = value or action.get("expected_title", "")
        return {"passed": actual_title == expected_title, "actual": actual_title, "expected": expected_title}
