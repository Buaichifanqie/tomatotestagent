from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from testagent.common.logging import get_logger
from testagent.harness.runners.base import BaseRunner, RunnerError

if TYPE_CHECKING:
    from testagent.harness.sandbox import ISandbox
    from testagent.models.result import TestResult

logger = get_logger(__name__)

LOCATOR_STRATEGIES = frozenset(
    {
        "id",
        "accessibility_id",
        "xpath",
        "class_name",
        "css",
        "uiautomator",
        "ios_class_chain",
        "ios_predicate",
    }
)

APPIUM_ACTIONS: frozenset[str] = frozenset(
    {
        "launch_app",
        "close_app",
        "restart_app",
        "click",
        "tap",
        "fill",
        "clear",
        "swipe",
        "scroll_down",
        "scroll_up",
        "wait_for_element",
        "screenshot",
        "get_text",
        "get_attribute",
        "is_visible",
        "assert_text",
        "assert_visible",
        "assert_attribute",
        "press_key",
        "back",
        "long_press",
    }
)

PLATFORM_NAMES = frozenset({"Android", "iOS"})

AUTOMATION_NAMES = frozenset({"UiAutomator2", "XCUITest"})

APPIUM_DEFAULT_PORT = 4723
APPIUM_DEFAULT_HOST = "127.0.0.1"


class AppiumRunner(BaseRunner):
    """App test Runner (V1.0, ADR-004).

    Executes mobile app tests via Appium Server inside a MicroVM sandbox.
    Hard timeout: 180s per AGENTS.md Harness rules.
    Resource quota: 4CPU/4GB per ADR-004.
    """

    runner_type = "app_test"

    def __init__(self) -> None:
        super().__init__()
        self._driver: Any = None
        self._action_log: list[dict[str, object]] = []
        self._screenshots: list[str] = []
        self._microvm_result: dict[str, object] | None = None
        self._platform_name: str = "Android"
        self._device_name: str = ""
        self._app_path: str = ""
        self._appium_server_url: str = f"http://{APPIUM_DEFAULT_HOST}:{APPIUM_DEFAULT_PORT}"
        self._automation_name: str = "UiAutomator2"

    async def setup(
        self,
        config: dict[str, object],
        sandbox: ISandbox | None = None,
        sandbox_id: str | None = None,
    ) -> None:
        await super().setup(config, sandbox=sandbox, sandbox_id=sandbox_id)
        self._validate_config(config, ["platform_name", "device_name", "app_path"])

        platform_name = config["platform_name"]
        if not isinstance(platform_name, str) or platform_name not in PLATFORM_NAMES:
            raise RunnerError(
                f"Unsupported platform: {platform_name}. Must be one of {PLATFORM_NAMES}",
                code="INVALID_PLATFORM",
            )
        self._platform_name = platform_name

        device_name = config["device_name"]
        if not isinstance(device_name, str) or not device_name.strip():
            raise RunnerError("device_name must be a non-empty string", code="INVALID_CONFIG")
        self._device_name = device_name

        app_path = config["app_path"]
        if not isinstance(app_path, str) or not app_path.strip():
            raise RunnerError("app_path must be a non-empty string", code="INVALID_CONFIG")
        self._app_path = app_path

        automation_name = config.get("automation_name")
        if isinstance(automation_name, str) and automation_name in AUTOMATION_NAMES:
            self._automation_name = automation_name
        else:
            self._automation_name = "UiAutomator2" if platform_name == "Android" else "XCUITest"

        appium_host = config.get("appium_host", APPIUM_DEFAULT_HOST)
        appium_port = config.get("appium_port", APPIUM_DEFAULT_PORT)
        host_str = str(appium_host) if isinstance(appium_host, str) else APPIUM_DEFAULT_HOST
        port_int = int(appium_port) if isinstance(appium_port, (int, float)) else APPIUM_DEFAULT_PORT
        self._appium_server_url = f"http://{host_str}:{port_int}"

        if self._in_docker_mode:
            logger.info(
                "AppiumRunner setup (MicroVM mode)",
                extra={
                    "platform_name": self._platform_name,
                    "device_name": self._device_name,
                    "app_path": self._app_path,
                },
            )
            return

        try:
            from appium.webdriver.webdriver import WebDriver as AppiumWebDriver

            desired_caps: dict[str, object] = {
                "platformName": self._platform_name,
                "deviceName": self._device_name,
                "app": self._app_path,
                "automationName": self._automation_name,
                "noReset": config.get("no_reset", False),
                "newCommandTimeout": 300,
            }
            extra_caps = config.get("capabilities", {})
            if isinstance(extra_caps, dict):
                desired_caps.update(extra_caps)

            self._driver = AppiumWebDriver(
                command_executor=self._appium_server_url,
                desired_capabilities=desired_caps,
            )
            logger.info(
                "AppiumRunner setup (local mode)",
                extra={
                    "platform_name": self._platform_name,
                    "device_name": self._device_name,
                    "appium_server_url": self._appium_server_url,
                },
            )
        except ImportError as err:
            raise RunnerError(
                "Appium Python Client not installed. Run: pip install Appium-Python-Client",
                code="APPIUM_NOT_INSTALLED",
            ) from err

    async def execute(self, test_script: str) -> TestResult:
        if self._in_docker_mode:
            return await self._execute_microvm(test_script)
        return await self._execute_local(test_script)

    async def _execute_local(self, test_script: str) -> TestResult:
        if self._driver is None:
            raise RunnerError("Runner not setup, call setup() first", code="RUNNER_NOT_SETUP")

        script = self._parse_script(test_script)
        actions = script.get("actions", [])
        assertion_results: dict[str, object] = {}

        start_ms = self._now_ms()

        try:
            for i, action in enumerate(actions):
                action_result = await self._execute_action(action, i)
                if action.get("assertion"):
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

    async def _execute_microvm(self, test_script: str) -> TestResult:
        script_content = self._generate_docker_exec_script(test_script)
        container_path = await self._write_script(script_content)

        from testagent.harness.sandbox import RESOURCE_PROFILES

        profile = RESOURCE_PROFILES.get("app_test")
        timeout = profile.timeout if profile else 180

        start_ms = self._now_ms()
        output = await self._run_in_sandbox(f"python3 {container_path}", timeout=timeout)
        duration_ms = self._now_ms() - start_ms

        return self._parse_docker_output(output, duration_ms=duration_ms)

    async def teardown(self) -> None:
        if self._in_docker_mode:
            logger.info("AppiumRunner teardown complete (MicroVM mode)")
            return
        try:
            if self._driver is not None:
                self._driver.quit()
                self._driver = None
        except Exception as e:
            logger.warning("Error during AppiumRunner teardown", extra={"error": str(e)})
        logger.info("AppiumRunner teardown complete")

    async def collect_results(self) -> TestResult:
        if self._microvm_result is not None:
            status = self._microvm_result.get("status", "passed")
            assertion_results = self._microvm_result.get("assertion_results", {})
            logs = str(self._microvm_result.get("logs", ""))
            artifacts = self._microvm_result.get("artifacts", {})
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
        platform_name = script.get("platform_name", self._platform_name)
        device_name = script.get("device_name", self._device_name)
        app_path = script.get("app_path", self._app_path)
        automation_name = script.get("automation_name", self._automation_name)
        appium_server_url = script.get("appium_server_url", self._appium_server_url)
        capabilities = script.get("capabilities", {})

        return self._build_microvm_script(
            actions_json,
            platform_name,
            device_name,
            app_path,
            automation_name,
            appium_server_url,
            capabilities,
        )

    @staticmethod
    def _build_microvm_script(
        actions_json: str,
        platform_name: str,
        device_name: str,
        app_path: str,
        automation_name: str,
        appium_server_url: str,
        capabilities: dict[str, object],
    ) -> str:
        capabilities_json = json.dumps(capabilities)
        return f"""import json, sys, traceback, os, tempfile, base64
from appium.webdriver.webdriver import WebDriver as AppiumWebDriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

actions = {actions_json}
platform_name = {platform_name!r}
device_name = {device_name!r}
app_path = {app_path!r}
automation_name = {automation_name!r}
appium_server_url = {appium_server_url!r}
extra_capabilities = {capabilities_json}

result = {{"logs": "", "assertion_results": {{}}, "artifacts": {{"screenshots": []}}}}
action_log = []

LOCATOR_MAP = {{
    "id": By.ID,
    "accessibility_id": By.ACCESSIBILITY_ID if hasattr(By, "ACCESSIBILITY_ID") else "accessibility id",
    "xpath": By.XPATH,
    "class_name": By.CLASS_NAME,
    "css": By.CSS_SELECTOR,
    "uiautomator": "uiautomator",
    "ios_class_chain": "ios class chain",
    "ios_predicate": "ios predicate",
}}

try:
    desired_caps = {{
        "platformName": platform_name,
        "deviceName": device_name,
        "app": app_path,
        "automationName": automation_name,
        "noReset": False,
        "newCommandTimeout": 300,
    }}
    desired_caps.update(extra_capabilities)

    driver = AppiumWebDriver(command_executor=appium_server_url, desired_capabilities=desired_caps)

    assertion_results = {{}}
    all_passed = True

    def _find_element(driver, action):
        strategy = action.get("strategy", "accessibility_id")
        selector = action.get("selector", "")
        timeout = action.get("timeout", 10000) / 1000.0
        by = LOCATOR_MAP.get(strategy, "accessibility id")
        wait = WebDriverWait(driver, timeout)
        return wait.until(EC.presence_of_element_located((by, selector)))

    def _capture_screenshot(driver):
        try:
            screenshot_b64 = driver.get_screenshot_as_base64()
            fname = f"screenshot_{{os.urandom(4).hex()}}.png"
            tmp_path = os.path.join(tempfile.gettempdir(), fname)
            with open(tmp_path, "wb") as f:
                f.write(base64.b64decode(screenshot_b64))
            result["artifacts"]["screenshots"].append(tmp_path)
            return tmp_path
        except Exception:
            return None

    for idx, action in enumerate(actions):
        action_type = action.get("action", "")
        entry = {{"action": action_type, "index": idx}}

        try:
            if action_type == "launch_app":
                pass

            elif action_type == "close_app":
                driver.close_app()

            elif action_type == "restart_app":
                driver.close_app()
                driver.launch_app()

            elif action_type == "click":
                el = _find_element(driver, action)
                el.click()

            elif action_type == "tap":
                x = action.get("x", 0)
                y = action.get("y", 0)
                driver.tap([(x, y)])

            elif action_type == "fill":
                value = action.get("value", "")
                el = _find_element(driver, action)
                el.send_keys(value)

            elif action_type == "clear":
                el = _find_element(driver, action)
                el.clear()

            elif action_type == "swipe":
                start_x = action.get("start_x", 0)
                start_y = action.get("start_y", 0)
                end_x = action.get("end_x", 0)
                end_y = action.get("end_y", 0)
                duration = action.get("duration", 1000)
                driver.swipe(start_x, start_y, end_x, end_y, duration)

            elif action_type == "scroll_down":
                width = driver.get_window_size()["width"]
                height = driver.get_window_size()["height"]
                driver.swipe(width // 2, height * 3 // 4, width // 2, height // 4, 800)

            elif action_type == "scroll_up":
                width = driver.get_window_size()["width"]
                height = driver.get_window_size()["height"]
                driver.swipe(width // 2, height // 4, width // 2, height * 3 // 4, 800)

            elif action_type == "wait_for_element":
                _find_element(driver, action)
                if action.get("assertion"):
                    akey = f"wait_for_element_{{idx}}"
                    assertion_results[akey] = {{"passed": True}}

            elif action_type == "screenshot":
                _capture_screenshot(driver)

            elif action_type == "get_text":
                el = _find_element(driver, action)
                text = el.text
                if action.get("assertion"):
                    expected = action.get("expected_text", "")
                    passed = text == expected
                    akey = f"get_text_{{idx}}"
                    assertion_results[akey] = {{"passed": passed, "actual": text, "expected": expected}}
                    if not passed:
                        all_passed = False

            elif action_type == "get_attribute":
                el = _find_element(driver, action)
                attr_name = action.get("attribute", "text")
                attr_value = el.get_attribute(attr_name)
                if action.get("assertion"):
                    expected = action.get("expected_value", "")
                    passed = attr_value == expected
                    akey = f"get_attribute_{{idx}}"
                    assertion_results[akey] = {{"passed": passed, "actual": attr_value, "expected": expected}}
                    if not passed:
                        all_passed = False

            elif action_type == "is_visible":
                el = _find_element(driver, action)
                visible = el.is_displayed()
                if action.get("assertion"):
                    akey = f"is_visible_{{idx}}"
                    assertion_results[akey] = {{"passed": visible, "visible": visible}}

            elif action_type == "assert_text":
                el = _find_element(driver, action)
                actual = el.text
                expected = action.get("expected_text", "")
                passed = actual == expected
                akey = f"assert_text_{{idx}}"
                assertion_results[akey] = {{"passed": passed, "actual": actual, "expected": expected}}
                if not passed:
                    all_passed = False

            elif action_type == "assert_visible":
                el = _find_element(driver, action)
                visible = el.is_displayed()
                expected = action.get("expected", True)
                passed = visible == expected
                akey = f"assert_visible_{{idx}}"
                assertion_results[akey] = {{"passed": passed, "visible": visible, "expected": expected}}
                if not passed:
                    all_passed = False

            elif action_type == "assert_attribute":
                el = _find_element(driver, action)
                attr_name = action.get("attribute", "text")
                actual = el.get_attribute(attr_name)
                expected = action.get("expected_value", "")
                passed = actual == expected
                akey = f"assert_attribute_{{idx}}"
                assertion_results[akey] = {{"passed": passed, "actual": actual, "expected": expected}}
                if not passed:
                    all_passed = False

            elif action_type == "press_key":
                key_code = action.get("key_code", 66)
                driver.press_keycode(key_code)

            elif action_type == "back":
                driver.back()

            elif action_type == "long_press":
                el = _find_element(driver, action)
                duration = action.get("duration", 1000)
                driver.long_press(el, duration)

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

    driver.quit()

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
            self._microvm_result = data
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
                assertion_results={"error": f"Failed to parse MicroVM output: {e}"},
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
        if action_type not in APPIUM_ACTIONS:
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
        timeout = action.get("timeout", 10000)

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
        if self._driver is None:
            return None
        try:
            import tempfile

            screenshot_b64 = self._driver.get_screenshot_as_base64()
            import base64

            screenshot_bytes = base64.b64decode(screenshot_b64)
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                f.write(screenshot_bytes)
                self._screenshots.append(f.name)
                return f.name
        except Exception as e:
            logger.warning("Failed to capture screenshot", extra={"error": str(e)})
            return None

    def _find_element(self, strategy: str, selector: str, timeout: int = 10000) -> Any:
        if self._driver is None:
            raise RunnerError("Driver not initialized", code="RUNNER_NOT_SETUP")

        try:
            from appium.webdriver.common.appiumby import AppiumBy
            from selenium.webdriver.support import expected_conditions
            from selenium.webdriver.support.ui import WebDriverWait

            by_map: dict[str, str] = {
                "id": AppiumBy.ID,
                "accessibility_id": AppiumBy.ACCESSIBILITY_ID,
                "xpath": AppiumBy.XPATH,
                "class_name": AppiumBy.CLASS_NAME,
                "css": AppiumBy.CSS_SELECTOR,
                "uiautomator": AppiumBy.ANDROID_UIAUTOMATOR,
                "ios_class_chain": AppiumBy.IOS_CLASS_CHAIN,
                "ios_predicate": AppiumBy.IOS_PREDICATE,
            }

            by = by_map.get(strategy, AppiumBy.ACCESSIBILITY_ID)
            wait_timeout = timeout / 1000.0
            wait = WebDriverWait(self._driver, wait_timeout)
            return wait.until(expected_conditions.presence_of_element_located((by, selector)))
        except ImportError as err:
            raise RunnerError(
                "Appium Python Client not installed",
                code="APPIUM_NOT_INSTALLED",
            ) from err

    async def _action_launch_app(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        self._driver.launch_app()
        return {"passed": True}

    async def _action_close_app(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        self._driver.close_app()
        return {"passed": True}

    async def _action_restart_app(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        self._driver.close_app()
        self._driver.launch_app()
        return {"passed": True}

    async def _action_click(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        strategy = action.get("strategy", "accessibility_id")
        el = self._find_element(strategy, selector, timeout)
        el.click()
        return {"passed": True}

    async def _action_tap(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        x = action.get("x", 0)
        y = action.get("y", 0)
        self._driver.tap([(x, y)])
        return {"passed": True}

    async def _action_fill(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        if value is None:
            raise RunnerError("'fill' action requires 'value' field", code="MISSING_ACTION_VALUE")
        strategy = action.get("strategy", "accessibility_id")
        el = self._find_element(strategy, selector, timeout)
        el.send_keys(value)
        return {"passed": True}

    async def _action_clear(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        strategy = action.get("strategy", "accessibility_id")
        el = self._find_element(strategy, selector, timeout)
        el.clear()
        return {"passed": True}

    async def _action_swipe(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        start_x = action.get("start_x", 0)
        start_y = action.get("start_y", 0)
        end_x = action.get("end_x", 0)
        end_y = action.get("end_y", 0)
        duration = action.get("duration", 1000)
        self._driver.swipe(start_x, start_y, end_x, end_y, duration)
        return {"passed": True}

    async def _action_scroll_down(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        size = self._driver.get_window_size()
        width = size["width"]
        height = size["height"]
        self._driver.swipe(width // 2, height * 3 // 4, width // 2, height // 4, 800)
        return {"passed": True}

    async def _action_scroll_up(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        size = self._driver.get_window_size()
        width = size["width"]
        height = size["height"]
        self._driver.swipe(width // 2, height // 4, width // 2, height * 3 // 4, 800)
        return {"passed": True}

    async def _action_wait_for_element(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        strategy = action.get("strategy", "accessibility_id")
        el = self._find_element(strategy, selector, timeout)
        return {"passed": el is not None}

    async def _action_screenshot(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        path = await self._capture_screenshot()
        return {"passed": True, "screenshot_path": path}

    async def _action_get_text(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        strategy = action.get("strategy", "accessibility_id")
        el = self._find_element(strategy, selector, timeout)
        text = el.text
        if action.get("assertion"):
            expected = action.get("expected_text", "")
            return {"passed": text == expected, "actual": text, "expected": expected}
        return {"passed": True, "text": text}

    async def _action_get_attribute(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        strategy = action.get("strategy", "accessibility_id")
        el = self._find_element(strategy, selector, timeout)
        attr_name = value or action.get("attribute", "text")
        attr_value = el.get_attribute(attr_name)
        if action.get("assertion"):
            expected = action.get("expected_value", "")
            return {"passed": attr_value == expected, "actual": attr_value, "expected": expected}
        return {"passed": True, "value": attr_value}

    async def _action_is_visible(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        strategy = action.get("strategy", "accessibility_id")
        el = self._find_element(strategy, selector, timeout)
        is_visible = el.is_displayed()
        return {"passed": is_visible, "selector": selector, "visible": is_visible}

    async def _action_assert_text(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        strategy = action.get("strategy", "accessibility_id")
        el = self._find_element(strategy, selector, timeout)
        actual = el.text
        expected = value or action.get("expected_text", "")
        return {"passed": actual == expected, "actual": actual, "expected": expected}

    async def _action_assert_visible(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        strategy = action.get("strategy", "accessibility_id")
        el = self._find_element(strategy, selector, timeout)
        is_visible = el.is_displayed()
        expected = action.get("expected", True)
        return {"passed": is_visible == expected, "visible": is_visible, "expected": expected}

    async def _action_assert_attribute(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        strategy = action.get("strategy", "accessibility_id")
        el = self._find_element(strategy, selector, timeout)
        attr_name = action.get("attribute", "text")
        actual = el.get_attribute(attr_name)
        expected = action.get("expected_value", "")
        return {"passed": actual == expected, "actual": actual, "expected": expected}

    async def _action_press_key(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        key_code = action.get("key_code", 66)
        self._driver.press_keycode(key_code)
        return {"passed": True}

    async def _action_back(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        self._driver.back()
        return {"passed": True}

    async def _action_long_press(
        self, selector: str, value: str | None, timeout: int, action: dict[str, Any]
    ) -> dict[str, object]:
        if self._driver is None:
            return {"passed": False, "error": "Driver not initialized"}
        strategy = action.get("strategy", "accessibility_id")
        el = self._find_element(strategy, selector, timeout)
        duration = action.get("duration", 1000)
        self._driver.long_press(el, duration)
        return {"passed": True}
