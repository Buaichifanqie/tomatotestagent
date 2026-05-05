from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from testagent.common.logging import get_logger
from testagent.harness.runners.base import BaseRunner, RunnerError

if TYPE_CHECKING:
    from testagent.harness.sandbox import ISandbox
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
        super().__init__()
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None
        self._headless: bool = True
        self._action_log: list[dict[str, object]] = []
        self._screenshots: list[str] = []
        self._docker_result: dict[str, object] | None = None
        self._browser_type: str = "chromium"

    async def setup(
        self,
        config: dict[str, object],
        sandbox: ISandbox | None = None,
        sandbox_id: str | None = None,
    ) -> None:
        await super().setup(config, sandbox=sandbox, sandbox_id=sandbox_id)
        self._validate_config(config, ["browser_type"])
        browser_type = config["browser_type"]
        if not isinstance(browser_type, str) or browser_type not in BROWSER_TYPES:
            raise RunnerError(
                f"Unsupported browser type: {browser_type}. Must be one of {BROWSER_TYPES}",
                code="INVALID_BROWSER_TYPE",
            )
        self._browser_type = browser_type

        if self._in_docker_mode:
            return

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
        if self._in_docker_mode:
            return await self._execute_docker(test_script)
        return await self._execute_local(test_script)

    async def _execute_local(self, test_script: str) -> TestResult:
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

    async def _execute_docker(self, test_script: str) -> TestResult:
        script_content = self._generate_docker_exec_script(test_script)
        container_path = await self._write_script(script_content)

        from testagent.harness.sandbox import RESOURCE_PROFILES

        profile = RESOURCE_PROFILES.get("web_test")
        timeout = profile.timeout if profile else 120

        start_ms = self._now_ms()
        output = await self._run_in_sandbox(f"python3 {container_path}", timeout=timeout)
        duration_ms = self._now_ms() - start_ms

        return self._parse_docker_output(output, duration_ms=duration_ms)

    async def teardown(self) -> None:
        if self._in_docker_mode:
            logger.info("PlaywrightRunner teardown complete (Docker mode)")
            return
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
        if self._docker_result is not None:
            status = self._docker_result.get("status", "passed")
            assertion_results = self._docker_result.get("assertion_results", {})
            logs = str(self._docker_result.get("logs", ""))
            artifacts = self._docker_result.get("artifacts", {})
            return self._make_result(
                status=str(status),
                logs=logs,
                assertion_results=assertion_results if isinstance(assertion_results, dict) else {},
                artifacts=artifacts if isinstance(artifacts, dict) else {},
            )
        combined_logs = json.dumps(self._action_log, ensure_ascii=False) if self._action_log else ""
        return self._make_result(
            status="passed",
            logs=combined_logs,
            artifacts={
                "total_actions": len(self._action_log),
                "screenshots": self._screenshots,
            },
        )

    def _generate_docker_exec_script(self, test_script: str) -> str:
        script = self._parse_script(test_script)
        actions_json = json.dumps(script.get("actions", []))
        browser_type = script.get("browser_type", self._browser_type)
        headless = script.get("headless", True)
        viewport = script.get("viewport", {"width": 1280, "height": 720})
        base_url = script.get("base_url", "")

        return self._build_docker_script(actions_json, browser_type, headless, viewport, base_url)

    @staticmethod
    def _build_docker_script(
        actions_json: str,
        browser_type: str,
        headless: bool,
        viewport: dict[str, object],
        base_url: str,
    ) -> str:
        return f"""import json, sys, traceback, os, tempfile
from playwright.sync_api import sync_playwright

actions = {actions_json}
browser_type = {browser_type!r}
headless = {json.dumps(headless)}
viewport = {json.dumps(viewport)}
base_url = {base_url!r}

result = {{"logs": "", "assertion_results": {{}}, "artifacts": {{"screenshots": []}}}}
action_log = []

try:
    with sync_playwright() as pw:
        browser_launcher = getattr(pw, browser_type)
        browser = browser_launcher.launch(headless=headless)
        context = browser.new_context(viewport=viewport, base_url=base_url)
        page = context.new_page()

        assertion_results = {{}}
        all_passed = True

        for idx, action in enumerate(actions):
            action_type = action.get("action", "")
            selector = action.get("selector", "")
            value = action.get("value")
            action_timeout = action.get("timeout", 30000)
            entry = {{"action": action_type, "selector": selector, "index": idx}}

            try:
                if action_type == "navigate":
                    url = value or action.get("url", "")
                    page.goto(url, wait_until="load", timeout=action_timeout)
                    if "assert_url" in action:
                        expected_url = action["assert_url"]
                        actual_url = page.url
                        passed = actual_url == expected_url
                        akey = f"navigate_assert_{{idx}}"
                        assertion_results[akey] = {{"passed": passed, "expected": expected_url, "actual": actual_url}}
                        if not passed:
                            all_passed = False

                elif action_type == "click":
                    page.click(selector, timeout=action_timeout)

                elif action_type == "fill":
                    if value is None:
                        raise ValueError("'fill' action requires 'value'")
                    page.fill(selector, value, timeout=action_timeout)

                elif action_type == "type":
                    if value is None:
                        raise ValueError("'type' action requires 'value'")
                    delay = action.get("delay", 0)
                    page.type(selector, value, delay=delay, timeout=action_timeout)

                elif action_type == "select":
                    if value is None:
                        raise ValueError("'select' action requires 'value'")
                    page.select_option(selector, value, timeout=action_timeout)

                elif action_type == "check":
                    page.check(selector, timeout=action_timeout)

                elif action_type == "uncheck":
                    page.uncheck(selector, timeout=action_timeout)

                elif action_type == "hover":
                    page.hover(selector, timeout=action_timeout)

                elif action_type == "wait_for_selector":
                    state = action.get("state", "visible")
                    element = page.wait_for_selector(selector, state=state, timeout=action_timeout)
                    if action.get("assertion"):
                        akey = f"wait_for_selector_{{idx}}"
                        assertion_results[akey] = {{"passed": element is not None}}

                elif action_type == "wait_for_navigation":
                    url_pattern = action.get("url")
                    if url_pattern:
                        page.wait_for_url(url_pattern, timeout=action_timeout)
                    else:
                        page.wait_for_load_state("networkidle", timeout=action_timeout)

                elif action_type == "screenshot":
                    screenshot_bytes = page.screenshot(full_page=True)
                    fname = f"screenshot_{{idx}}_{{os.urandom(4).hex()}}.png"
                    tmp_path = os.path.join(tempfile.gettempdir(), fname)
                    with open(tmp_path, "wb") as f:
                        f.write(screenshot_bytes)
                    result["artifacts"]["screenshots"].append(tmp_path)

                elif action_type == "evaluate":
                    expression = value or action.get("expression", "")
                    page.evaluate(expression)

                elif action_type == "get_text":
                    text = page.text_content(selector, timeout=action_timeout)
                    if action.get("assertion"):
                        expected = action.get("expected_text", "")
                        passed = text == expected
                        akey = f"get_text_{{idx}}"
                        assertion_results[akey] = {{"passed": passed, "actual": text, "expected": expected}}
                        if not passed:
                            all_passed = False

                elif action_type == "get_attribute":
                    attr_name = value or action.get("attribute", "")
                    attr_value = page.get_attribute(selector, attr_name, timeout=action_timeout)
                    if action.get("assertion"):
                        expected = action.get("expected_value", "")
                        passed = attr_value == expected
                        akey = f"get_attr_{{idx}}"
                        assertion_results[akey] = {{"passed": passed, "actual": attr_value, "expected": expected}}
                        if not passed:
                            all_passed = False

                elif action_type == "is_visible":
                    visible = page.is_visible(selector, timeout=action_timeout)
                    if action.get("assertion"):
                        akey = f"is_visible_{{idx}}"
                        assertion_results[akey] = {{"passed": visible, "visible": visible}}

                elif action_type == "assert_text":
                    actual = page.text_content(selector, timeout=action_timeout)
                    expected = value or action.get("expected_text", "")
                    passed = actual == expected
                    akey = f"assert_text_{{idx}}"
                    assertion_results[akey] = {{"passed": passed, "actual": actual, "expected": expected}}
                    if not passed:
                        all_passed = False

                elif action_type == "assert_visible":
                    visible = page.is_visible(selector, timeout=action_timeout)
                    expected = action.get("expected", True)
                    passed = visible == expected
                    akey = f"assert_visible_{{idx}}"
                    assertion_results[akey] = {{"passed": passed, "visible": visible, "expected": expected}}
                    if not passed:
                        all_passed = False

                elif action_type == "assert_url":
                    actual_url = page.url
                    expected_url = value or action.get("expected_url", "")
                    passed = actual_url == expected_url
                    akey = f"assert_url_{{idx}}"
                    assertion_results[akey] = {{"passed": passed, "actual": actual_url, "expected": expected_url}}
                    if not passed:
                        all_passed = False

                elif action_type == "assert_title":
                    actual_title = page.title()
                    expected_title = value or action.get("expected_title", "")
                    passed = actual_title == expected_title
                    akey = f"assert_title_{{idx}}"
                    assertion_results[akey] = {{"passed": passed, "actual": actual_title, "expected": expected_title}}
                    if not passed:
                        all_passed = False

                else:
                    raise ValueError(f"Unknown action: {{action_type}}")

                action_log.append(entry)

            except Exception as action_err:
                entry["error"] = str(action_err)
                action_log.append(entry)
                if action.get("assertion"):
                    akey = f"action_{{idx}}"
                    assertion_results[akey] = {{"passed": False, "error": str(action_err)}}
                    all_passed = False

        browser.close()

        if not assertion_results:
            assertion_results["executed"] = {{"passed": True, "info": "No assertions defined"}}

        result["assertion_results"] = assertion_results
        result["status"] = "passed" if all_passed else "failed"
        result["logs"] = json.dumps(action_log)

except Exception as e:
    result["status"] = "error"
    result["assertion_results"] = {{"error": str(e)}}
    result["logs"] = traceback.format_exc()

print(json.dumps(result))
"""

    def _parse_docker_output(
        self,
        output: dict[str, object],
        *,
        duration_ms: float = 0.0,
    ) -> TestResult:
        stdout = str(output.get("stdout", "")).strip()
        stderr = str(output.get("stderr", "")).strip()
        exit_code = output.get("exit_code", 0)
        if isinstance(exit_code, int) and exit_code != 0:
            return self._make_result(
                status="error",
                duration_ms=round(duration_ms, 2),
                logs=stderr or f"Non-zero exit code: {exit_code}",
                assertion_results={"error": stderr or f"exit_code={exit_code}"},
            )

        try:
            data = json.loads(stdout)
            self._docker_result = data
            status = str(data.get("status", "passed"))
            assertion_results = data.get("assertion_results", {})
            logs = str(data.get("logs", ""))
            artifacts = data.get("artifacts", {})
            return self._make_result(
                status=status,
                duration_ms=round(duration_ms, 2),
                assertion_results=assertion_results if isinstance(assertion_results, dict) else {},
                logs=logs,
                artifacts=artifacts if isinstance(artifacts, dict) else {},
            )
        except (json.JSONDecodeError, ValueError) as e:
            return self._make_result(
                status="error",
                duration_ms=round(duration_ms, 2),
                logs=stdout,
                assertion_results={"error": f"Failed to parse Docker output: {e}"},
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
