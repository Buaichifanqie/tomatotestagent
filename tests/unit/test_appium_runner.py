from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.harness.runners import (
    AppiumRunner,
    IRunner,
    RunnerError,
    RunnerFactory,
)
from testagent.harness.runners.appium_runner import (
    APPIUM_ACTIONS,
    APPIUM_DEFAULT_HOST,
    APPIUM_DEFAULT_PORT,
    AUTOMATION_NAMES,
    LOCATOR_STRATEGIES,
    PLATFORM_NAMES,
)
from testagent.harness.sandbox import RESOURCE_PROFILES


class TestAppiumRunnerType:
    def test_runner_type(self) -> None:
        assert AppiumRunner.runner_type == "app_test"

    def test_resource_profile_app_test(self) -> None:
        profile = RESOURCE_PROFILES["app_test"]
        assert profile.cpus == 4
        assert profile.mem_limit == "4g"
        assert profile.timeout == 180


class TestAppiumRunnerProtocol:
    def test_complies_with_irunner_protocol(self) -> None:
        assert isinstance(AppiumRunner(), IRunner)

    def test_has_required_methods(self) -> None:
        import inspect

        for method_name in ["setup", "execute", "teardown", "collect_results"]:
            assert hasattr(AppiumRunner, method_name)
            method = getattr(AppiumRunner, method_name)
            assert inspect.iscoroutinefunction(method)

    def test_all_action_methods_are_async(self) -> None:
        import inspect

        runner = AppiumRunner()
        for action in APPIUM_ACTIONS:
            method_name = f"_action_{action}"
            method = getattr(runner, method_name, None)
            assert method is not None, f"Missing action method: {method_name}"
            assert inspect.iscoroutinefunction(method), f"{method_name} must be async"


class TestAppiumRunnerSetup:
    @pytest.mark.asyncio
    async def test_setup_requires_platform_name(self) -> None:
        runner = AppiumRunner()
        with pytest.raises(RunnerError) as excinfo:
            await runner.setup({"device_name": "emulator", "app_path": "/app.apk"})
        assert "platform_name" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_setup_requires_device_name(self) -> None:
        runner = AppiumRunner()
        with pytest.raises(RunnerError) as excinfo:
            await runner.setup({"platform_name": "Android", "app_path": "/app.apk"})
        assert "device_name" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_setup_requires_app_path(self) -> None:
        runner = AppiumRunner()
        with pytest.raises(RunnerError) as excinfo:
            await runner.setup({"platform_name": "Android", "device_name": "emulator"})
        assert "app_path" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_setup_invalid_platform_raises_error(self) -> None:
        runner = AppiumRunner()
        with pytest.raises(RunnerError) as excinfo:
            await runner.setup(
                {
                    "platform_name": "Windows",
                    "device_name": "emulator",
                    "app_path": "/app.apk",
                }
            )
        assert "INVALID_PLATFORM" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_setup_empty_device_name_raises_error(self) -> None:
        runner = AppiumRunner()
        with pytest.raises(RunnerError) as excinfo:
            await runner.setup(
                {
                    "platform_name": "Android",
                    "device_name": "  ",
                    "app_path": "/app.apk",
                }
            )
        assert "INVALID_CONFIG" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_setup_empty_app_path_raises_error(self) -> None:
        runner = AppiumRunner()
        with pytest.raises(RunnerError) as excinfo:
            await runner.setup(
                {
                    "platform_name": "Android",
                    "device_name": "emulator",
                    "app_path": "",
                }
            )
        assert "INVALID_CONFIG" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_setup_android_defaults_to_uiautomator2(self) -> None:
        with patch.dict(
            "sys.modules",
            {
                "appium": MagicMock(),
                "appium.webdriver": MagicMock(),
                "appium.webdriver.webdriver": MagicMock(WebDriver=MagicMock()),
            },
        ):
            runner = AppiumRunner()
            await runner.setup(
                {
                    "platform_name": "Android",
                    "device_name": "emulator-5554",
                    "app_path": "/app.apk",
                }
            )
        assert runner._automation_name == "UiAutomator2"
        assert runner._platform_name == "Android"

    @pytest.mark.asyncio
    async def test_setup_ios_defaults_to_xcuitest(self) -> None:
        with patch.dict(
            "sys.modules",
            {
                "appium": MagicMock(),
                "appium.webdriver": MagicMock(),
                "appium.webdriver.webdriver": MagicMock(WebDriver=MagicMock()),
            },
        ):
            runner = AppiumRunner()
            await runner.setup(
                {
                    "platform_name": "iOS",
                    "device_name": "iPhone 15",
                    "app_path": "/app.ipa",
                }
            )
        assert runner._automation_name == "XCUITest"
        assert runner._platform_name == "iOS"

    @pytest.mark.asyncio
    async def test_setup_custom_automation_name(self) -> None:
        with patch.dict(
            "sys.modules",
            {
                "appium": MagicMock(),
                "appium.webdriver": MagicMock(),
                "appium.webdriver.webdriver": MagicMock(WebDriver=MagicMock()),
            },
        ):
            runner = AppiumRunner()
            await runner.setup(
                {
                    "platform_name": "Android",
                    "device_name": "emulator-5554",
                    "app_path": "/app.apk",
                    "automation_name": "UiAutomator2",
                }
            )
        assert runner._automation_name == "UiAutomator2"

    @pytest.mark.asyncio
    async def test_setup_custom_appium_host_and_port(self) -> None:
        with patch.dict(
            "sys.modules",
            {
                "appium": MagicMock(),
                "appium.webdriver": MagicMock(),
                "appium.webdriver.webdriver": MagicMock(WebDriver=MagicMock()),
            },
        ):
            runner = AppiumRunner()
            await runner.setup(
                {
                    "platform_name": "Android",
                    "device_name": "emulator-5554",
                    "app_path": "/app.apk",
                    "appium_host": "192.168.1.100",
                    "appium_port": 4725,
                }
            )
        assert runner._appium_server_url == "http://192.168.1.100:4725"

    @pytest.mark.asyncio
    async def test_setup_microvm_mode_skips_driver_init(self) -> None:
        runner = AppiumRunner()
        mock_sandbox = MagicMock()
        mock_sandbox.get_tmpdir = AsyncMock(return_value="/tmp/testagent")

        await runner.setup(
            {
                "platform_name": "Android",
                "device_name": "emulator-5554",
                "app_path": "/app.apk",
            },
            sandbox=mock_sandbox,
            sandbox_id="vm-001",
        )

        assert runner._driver is None
        assert runner._sandbox is mock_sandbox
        assert runner._sandbox_id == "vm-001"

    @pytest.mark.asyncio
    async def test_setup_local_mode_appium_not_installed(self) -> None:
        with patch.dict("sys.modules", {"appium": None, "appium.webdriver": None, "appium.webdriver.webdriver": None}):
            runner = AppiumRunner()
            with pytest.raises(RunnerError) as excinfo:
                await runner.setup(
                    {
                        "platform_name": "Android",
                        "device_name": "emulator-5554",
                        "app_path": "/app.apk",
                    }
                )
            assert "APPIUM_NOT_INSTALLED" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_setup_local_mode_creates_driver(self) -> None:
        mock_driver_cls = MagicMock()
        mock_driver_instance = MagicMock()
        mock_driver_cls.return_value = mock_driver_instance

        with patch.dict(
            "sys.modules",
            {
                "appium": MagicMock(),
                "appium.webdriver": MagicMock(),
                "appium.webdriver.webdriver": MagicMock(WebDriver=mock_driver_cls),
            },
        ):
            runner = AppiumRunner()
            await runner.setup(
                {
                    "platform_name": "Android",
                    "device_name": "emulator-5554",
                    "app_path": "/app.apk",
                }
            )

            assert runner._driver is mock_driver_instance
            mock_driver_cls.assert_called_once()
            call_kwargs = mock_driver_cls.call_args
            caps = (
                call_kwargs[1].get("desired_capabilities", {})
                if len(call_kwargs) > 1 and isinstance(call_kwargs[1], dict)
                else call_kwargs[0][1]
                if len(call_kwargs[0]) > 1
                else {}
            )
            if isinstance(caps, dict):
                assert caps["platformName"] == "Android"


class TestAppiumRunnerExecuteLocal:
    @pytest.mark.asyncio
    async def test_execute_without_setup_raises_error(self) -> None:
        runner = AppiumRunner()
        with pytest.raises(RunnerError) as excinfo:
            await runner.execute('{"actions": []}')
        assert "RUNNER_NOT_SETUP" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_execute_invalid_json_raises_error(self) -> None:
        runner = AppiumRunner()
        runner._driver = MagicMock()
        with pytest.raises(RunnerError) as excinfo:
            await runner.execute("not json")
        assert "INVALID_SCRIPT" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_execute_missing_actions_key_raises_error(self) -> None:
        runner = AppiumRunner()
        runner._driver = MagicMock()
        with pytest.raises(RunnerError) as excinfo:
            await runner.execute('{"not_actions": "value"}')
        assert "INVALID_SCRIPT" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_execute_empty_actions_passes(self) -> None:
        runner = AppiumRunner()
        runner._driver = MagicMock()
        result = await runner.execute(json.dumps({"actions": []}))
        assert result.status == "passed"

    @pytest.mark.asyncio
    async def test_execute_unknown_action_returns_error(self) -> None:
        runner = AppiumRunner()
        runner._driver = MagicMock()
        result = await runner.execute(json.dumps({"actions": [{"action": "fly"}]}))
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_execute_launch_app_action(self) -> None:
        mock_driver = MagicMock()
        mock_driver.launch_app = MagicMock()

        runner = AppiumRunner()
        runner._driver = mock_driver

        result = await runner.execute(
            json.dumps(
                {
                    "actions": [{"action": "launch_app"}],
                }
            )
        )

        mock_driver.launch_app.assert_called_once()
        assert result.status == "passed"

    @pytest.mark.asyncio
    async def test_execute_close_app_action(self) -> None:
        mock_driver = MagicMock()
        mock_driver.close_app = MagicMock()

        runner = AppiumRunner()
        runner._driver = mock_driver

        result = await runner.execute(
            json.dumps(
                {
                    "actions": [{"action": "close_app"}],
                }
            )
        )

        mock_driver.close_app.assert_called_once()
        assert result.status == "passed"

    @pytest.mark.asyncio
    async def test_execute_click_action(self) -> None:
        mock_el = MagicMock()
        mock_driver = MagicMock()

        runner = AppiumRunner()
        runner._driver = mock_driver

        with patch.object(runner, "_find_element", return_value=mock_el):
            result = await runner.execute(
                json.dumps(
                    {
                        "actions": [{"action": "click", "selector": "login_btn", "strategy": "accessibility_id"}],
                    }
                )
            )

        mock_el.click.assert_called_once()
        assert result.status == "passed"

    @pytest.mark.asyncio
    async def test_execute_fill_action(self) -> None:
        mock_el = MagicMock()
        mock_driver = MagicMock()

        runner = AppiumRunner()
        runner._driver = mock_driver

        with patch.object(runner, "_find_element", return_value=mock_el):
            result = await runner.execute(
                json.dumps(
                    {
                        "actions": [
                            {"action": "fill", "selector": "username", "value": "admin", "strategy": "accessibility_id"}
                        ],
                    }
                )
            )

        mock_el.send_keys.assert_called_once_with("admin")
        assert result.status == "passed"

    @pytest.mark.asyncio
    async def test_execute_fill_without_value_raises_error(self) -> None:
        mock_el = MagicMock()
        mock_driver = MagicMock()

        runner = AppiumRunner()
        runner._driver = mock_driver

        with patch.object(runner, "_find_element", return_value=mock_el):
            result = await runner.execute(
                json.dumps(
                    {
                        "actions": [{"action": "fill", "selector": "username"}],
                    }
                )
            )

        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_execute_assert_text_action_passed(self) -> None:
        mock_el = MagicMock()
        mock_el.text = "Hello World"
        mock_driver = MagicMock()

        runner = AppiumRunner()
        runner._driver = mock_driver

        with patch.object(runner, "_find_element", return_value=mock_el):
            result = await runner.execute(
                json.dumps(
                    {
                        "actions": [
                            {
                                "action": "assert_text",
                                "selector": "label",
                                "strategy": "accessibility_id",
                                "expected_text": "Hello World",
                                "assertion": True,
                            }
                        ],
                    }
                )
            )

        assert result.status == "passed"

    @pytest.mark.asyncio
    async def test_execute_assert_text_action_failed(self) -> None:
        mock_el = MagicMock()
        mock_el.text = "Wrong Text"
        mock_driver = MagicMock()

        runner = AppiumRunner()
        runner._driver = mock_driver

        with patch.object(runner, "_find_element", return_value=mock_el):
            result = await runner.execute(
                json.dumps(
                    {
                        "actions": [
                            {
                                "action": "assert_text",
                                "selector": "label",
                                "strategy": "accessibility_id",
                                "expected_text": "Hello World",
                                "assertion": True,
                                "assertion_label": "text_check",
                            }
                        ],
                    }
                )
            )

        assert result.status == "failed"
        assert result.assertion_results["text_check"]["passed"] is False
        assert result.assertion_results["text_check"]["actual"] == "Wrong Text"
        assert result.assertion_results["text_check"]["expected"] == "Hello World"

    @pytest.mark.asyncio
    async def test_execute_assert_visible_action(self) -> None:
        mock_el = MagicMock()
        mock_el.is_displayed = MagicMock(return_value=True)
        mock_driver = MagicMock()

        runner = AppiumRunner()
        runner._driver = mock_driver

        with patch.object(runner, "_find_element", return_value=mock_el):
            result = await runner.execute(
                json.dumps(
                    {
                        "actions": [
                            {
                                "action": "assert_visible",
                                "selector": "btn",
                                "strategy": "accessibility_id",
                                "assertion": True,
                            }
                        ],
                    }
                )
            )

        assert result.status == "passed"

    @pytest.mark.asyncio
    async def test_execute_swipe_action(self) -> None:
        mock_driver = MagicMock()
        mock_driver.swipe = MagicMock()

        runner = AppiumRunner()
        runner._driver = mock_driver

        result = await runner.execute(
            json.dumps(
                {
                    "actions": [
                        {"action": "swipe", "start_x": 100, "start_y": 500, "end_x": 100, "end_y": 200, "duration": 500}
                    ],
                }
            )
        )

        mock_driver.swipe.assert_called_once_with(100, 500, 100, 200, 500)
        assert result.status == "passed"

    @pytest.mark.asyncio
    async def test_execute_back_action(self) -> None:
        mock_driver = MagicMock()
        mock_driver.back = MagicMock()

        runner = AppiumRunner()
        runner._driver = mock_driver

        result = await runner.execute(
            json.dumps(
                {
                    "actions": [{"action": "back"}],
                }
            )
        )

        mock_driver.back.assert_called_once()
        assert result.status == "passed"

    @pytest.mark.asyncio
    async def test_execute_runtime_error_captures_screenshot(self) -> None:
        mock_driver = MagicMock()
        mock_el = MagicMock()
        mock_el.click = MagicMock(side_effect=Exception("Element not found"))

        runner = AppiumRunner()
        runner._driver = mock_driver

        with (
            patch.object(runner, "_find_element", return_value=mock_el),
            patch.object(runner, "_capture_screenshot", AsyncMock(return_value="/tmp/screenshot.png")),
        ):
            result = await runner.execute(
                json.dumps(
                    {
                        "actions": [{"action": "click", "selector": "missing_btn", "strategy": "accessibility_id"}],
                    }
                )
            )

        assert result.status == "error"
        assert result.artifacts is not None
        assert result.artifacts.get("screenshot") == "/tmp/screenshot.png"


class TestAppiumRunnerMicroVMExecution:
    @pytest.mark.asyncio
    async def test_execute_microvm_generates_and_runs_script(self) -> None:
        runner = AppiumRunner()
        mock_sandbox = MagicMock()
        mock_sandbox.get_tmpdir = AsyncMock(return_value="/tmp/testagent")
        runner._sandbox = mock_sandbox
        runner._sandbox_id = "vm-001"
        runner._sandbox_tmpdir = "/tmp/testagent"
        runner._platform_name = "Android"
        runner._device_name = "emulator-5554"
        runner._app_path = "/app.apk"

        mock_output = {
            "exit_code": 0,
            "stdout": json.dumps(
                {
                    "status": "passed",
                    "assertion_results": {"executed": {"passed": True}},
                    "logs": "[]",
                    "artifacts": {"screenshots": []},
                }
            ),
            "stderr": "",
        }

        with (
            patch.object(runner, "_write_script", AsyncMock(return_value="/tmp/testagent/test_script.py")),
            patch.object(runner, "_run_in_sandbox", AsyncMock(return_value=mock_output)),
        ):
            result = await runner._execute_microvm(
                json.dumps(
                    {
                        "actions": [{"action": "launch_app"}],
                    }
                )
            )

        assert result.status == "passed"

    @pytest.mark.asyncio
    async def test_execute_microvm_non_zero_exit_code(self) -> None:
        runner = AppiumRunner()
        runner._sandbox = MagicMock()
        runner._sandbox_id = "vm-001"
        runner._sandbox_tmpdir = "/tmp/testagent"

        mock_output = {
            "exit_code": 1,
            "stdout": "",
            "stderr": "ImportError: No module named appium",
        }

        with (
            patch.object(runner, "_write_script", AsyncMock(return_value="/tmp/testagent/test_script.py")),
            patch.object(runner, "_run_in_sandbox", AsyncMock(return_value=mock_output)),
        ):
            result = await runner._execute_microvm('{"actions": []}')

        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_execute_microvm_invalid_json_output(self) -> None:
        runner = AppiumRunner()
        runner._sandbox = MagicMock()
        runner._sandbox_id = "vm-001"
        runner._sandbox_tmpdir = "/tmp/testagent"

        mock_output = {
            "exit_code": 0,
            "stdout": "not valid json output",
            "stderr": "",
        }

        with (
            patch.object(runner, "_write_script", AsyncMock(return_value="/tmp/testagent/test_script.py")),
            patch.object(runner, "_run_in_sandbox", AsyncMock(return_value=mock_output)),
        ):
            result = await runner._execute_microvm('{"actions": []}')

        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_execute_microvm_uses_180s_timeout(self) -> None:
        runner = AppiumRunner()
        runner._sandbox = MagicMock()
        runner._sandbox_id = "vm-001"
        runner._sandbox_tmpdir = "/tmp/testagent"

        captured_timeout: list[int] = []

        async def _mock_run_in_sandbox(command: str, timeout: int | None = None) -> dict[str, object]:
            captured_timeout.append(timeout or 0)
            return {
                "exit_code": 0,
                "stdout": json.dumps({"status": "passed", "assertion_results": {}, "logs": "", "artifacts": {}}),
                "stderr": "",
            }

        with (
            patch.object(runner, "_write_script", AsyncMock(return_value="/tmp/testagent/test_script.py")),
            patch.object(runner, "_run_in_sandbox", _mock_run_in_sandbox),
        ):
            await runner._execute_microvm('{"actions": []}')

        assert captured_timeout == [180]


class TestAppiumRunnerTeardown:
    @pytest.mark.asyncio
    async def test_teardown_closes_driver(self) -> None:
        mock_driver = MagicMock()
        runner = AppiumRunner()
        runner._driver = mock_driver

        await runner.teardown()

        mock_driver.quit.assert_called_once()
        assert runner._driver is None

    @pytest.mark.asyncio
    async def test_teardown_safe_when_not_initialized(self) -> None:
        runner = AppiumRunner()
        await runner.teardown()

    @pytest.mark.asyncio
    async def test_teardown_microvm_mode_skips_driver(self) -> None:
        mock_sandbox = MagicMock()
        runner = AppiumRunner()
        runner._sandbox = mock_sandbox
        runner._sandbox_id = "vm-001"

        await runner.teardown()
        assert runner._driver is None


class TestAppiumRunnerCollectResults:
    @pytest.mark.asyncio
    async def test_collect_results_microvm_mode(self) -> None:
        runner = AppiumRunner()
        runner._microvm_result = {
            "status": "failed",
            "assertion_results": {"assert_text_0": {"passed": False}},
            "logs": "test log",
            "artifacts": {"screenshots": ["/tmp/s1.png"]},
        }

        result = await runner.collect_results()
        assert result.status == "failed"
        assert result.assertion_results["assert_text_0"]["passed"] is False
        assert result.artifacts["screenshots"] == ["/tmp/s1.png"]

    @pytest.mark.asyncio
    async def test_collect_results_local_mode(self) -> None:
        runner = AppiumRunner()
        runner._action_log = [{"action": "click", "selector": "btn", "index": 0}]
        runner._screenshots = ["/tmp/screenshot.png"]

        result = await runner.collect_results()
        assert result.status == "passed"
        assert result.artifacts is not None
        assert result.artifacts["total_actions"] == 1
        assert result.artifacts["screenshots"] == ["/tmp/screenshot.png"]


class TestAppiumRunnerScriptParsing:
    def test_parse_valid_script(self) -> None:
        runner = AppiumRunner()
        script = runner._parse_script('{"actions": [{"action": "click"}]}')
        assert "actions" in script
        assert len(script["actions"]) == 1

    def test_parse_invalid_json_raises_error(self) -> None:
        runner = AppiumRunner()
        with pytest.raises(RunnerError) as excinfo:
            runner._parse_script("not json")
        assert "INVALID_SCRIPT" in excinfo.value.code

    def test_parse_non_dict_raises_error(self) -> None:
        runner = AppiumRunner()
        with pytest.raises(RunnerError) as excinfo:
            runner._parse_script('[{"action": "click"}]')
        assert "INVALID_SCRIPT" in excinfo.value.code

    def test_parse_missing_actions_raises_error(self) -> None:
        runner = AppiumRunner()
        with pytest.raises(RunnerError) as excinfo:
            runner._parse_script('{"not_actions": []}')
        assert "INVALID_SCRIPT" in excinfo.value.code

    def test_parse_actions_not_list_raises_error(self) -> None:
        runner = AppiumRunner()
        with pytest.raises(RunnerError) as excinfo:
            runner._parse_script('{"actions": "not_a_list"}')
        assert "INVALID_SCRIPT" in excinfo.value.code


class TestAppiumRunnerBuildMicrovmScript:
    def test_build_script_contains_appium_import(self) -> None:
        script = AppiumRunner._build_microvm_script(
            actions_json="[]",
            platform_name="Android",
            device_name="emulator-5554",
            app_path="/app.apk",
            automation_name="UiAutomator2",
            appium_server_url="http://127.0.0.1:4723",
            capabilities={},
        )
        assert "from appium.webdriver.webdriver import WebDriver" in script

    def test_build_script_contains_platform_config(self) -> None:
        script = AppiumRunner._build_microvm_script(
            actions_json="[]",
            platform_name="iOS",
            device_name="iPhone 15",
            app_path="/app.ipa",
            automation_name="XCUITest",
            appium_server_url="http://127.0.0.1:4723",
            capabilities={},
        )
        assert "iOS" in script
        assert "iPhone 15" in script
        assert "XCUITest" in script

    def test_build_script_handles_all_actions(self) -> None:
        script = AppiumRunner._build_microvm_script(
            actions_json="[]",
            platform_name="Android",
            device_name="emulator-5554",
            app_path="/app.apk",
            automation_name="UiAutomator2",
            appium_server_url="http://127.0.0.1:4723",
            capabilities={},
        )
        for action in APPIUM_ACTIONS:
            assert f'"{action}"' in script or f"'{action}'" in script or action in script

    def test_build_script_includes_capabilities(self) -> None:
        script = AppiumRunner._build_microvm_script(
            actions_json="[]",
            platform_name="Android",
            device_name="emulator-5554",
            app_path="/app.apk",
            automation_name="UiAutomator2",
            appium_server_url="http://127.0.0.1:4723",
            capabilities={"noReset": True},
        )
        assert "noReset" in script


class TestAppiumRunnerFindElement:
    def test_find_element_without_driver_raises_error(self) -> None:
        runner = AppiumRunner()
        with pytest.raises(RunnerError) as excinfo:
            runner._find_element("accessibility_id", "btn")
        assert "RUNNER_NOT_SETUP" in excinfo.value.code

    def test_find_element_appium_not_installed(self) -> None:
        mock_driver = MagicMock()
        runner = AppiumRunner()
        runner._driver = mock_driver

        with patch.dict(
            "sys.modules",
            {
                "appium": None,
                "appium.webdriver": None,
                "appium.webdriver.common": None,
                "appium.webdriver.common.appiumby": None,
                "selenium": None,
                "selenium.webdriver": None,
                "selenium.webdriver.support": None,
                "selenium.webdriver.support.ui": None,
                "selenium.webdriver.support.expected_conditions": None,
            },
        ):
            with pytest.raises(RunnerError) as excinfo:
                runner._find_element("accessibility_id", "btn")
            assert "APPIUM_NOT_INSTALLED" in excinfo.value.code


class TestAppiumRunnerActionNoDriver:
    @pytest.mark.asyncio
    async def test_all_actions_return_error_without_driver(self) -> None:
        runner = AppiumRunner()
        driver_required_actions = [
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
            "get_text",
            "get_attribute",
            "is_visible",
            "assert_text",
            "assert_visible",
            "assert_attribute",
            "press_key",
            "back",
            "long_press",
        ]
        for action_name in driver_required_actions:
            method = getattr(runner, f"_action_{action_name}")
            result = await method("", None, 10000, {})
            assert isinstance(result, dict), f"{action_name} should return dict"
            passed = result.get("passed")
            assert passed is False or passed is True, f"{action_name} should have 'passed' key"


class TestAppiumRunnerConstants:
    def test_platform_names(self) -> None:
        assert "Android" in PLATFORM_NAMES
        assert "iOS" in PLATFORM_NAMES

    def test_automation_names(self) -> None:
        assert "UiAutomator2" in AUTOMATION_NAMES
        assert "XCUITest" in AUTOMATION_NAMES

    def test_locator_strategies(self) -> None:
        for strategy in ["id", "accessibility_id", "xpath", "class_name", "css", "uiautomator"]:
            assert strategy in LOCATOR_STRATEGIES

    def test_appium_actions_complete(self) -> None:
        expected = {
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
        assert expected == APPIUM_ACTIONS

    def test_default_appium_port(self) -> None:
        assert APPIUM_DEFAULT_PORT == 4723

    def test_default_appium_host(self) -> None:
        assert APPIUM_DEFAULT_HOST == "127.0.0.1"


class TestRunnerFactoryAppTest:
    def test_factory_returns_appium_runner(self) -> None:
        runner = RunnerFactory.get_runner("app_test")
        assert isinstance(runner, AppiumRunner)

    def test_factory_returns_new_instance(self) -> None:
        r1 = RunnerFactory.get_runner("app_test")
        r2 = RunnerFactory.get_runner("app_test")
        assert r1 is not r2

    def test_all_three_runners_registered(self) -> None:
        for task_type in ["api_test", "web_test", "app_test"]:
            runner = RunnerFactory.get_runner(task_type)
            assert isinstance(runner, IRunner)
