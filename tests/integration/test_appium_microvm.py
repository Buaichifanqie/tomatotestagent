from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.common.errors import SandboxTimeoutError
from testagent.harness import (
    ISandbox,
    IsolationLevel,
    MicroVMSandbox,
    SandboxFactory,
)
from testagent.harness.orchestrator import HarnessOrchestrator
from testagent.harness.runners import AppiumRunner
from testagent.harness.sandbox import RESOURCE_PROFILES
from testagent.models.plan import TestTask

_HAS_KVM = os.path.exists("/dev/kvm")
_HAS_FIRECRACKER = shutil.which("firecracker") is not None

requires_kvm = pytest.mark.skipif(
    not (_HAS_KVM and _HAS_FIRECRACKER),
    reason="KVM/Firecracker not available on this host",
)


def _make_app_task(
    *,
    task_id: str = "task-app-001",
    task_type: str = "app_test",
    isolation_level: str = "",
    status: str = "queued",
    task_config: dict[str, object] | None = None,
) -> TestTask:
    return TestTask(
        id=task_id,
        plan_id="plan-001",
        task_type=task_type,
        isolation_level=isolation_level,
        priority=1,
        status=status,
        retry_count=0,
        task_config=task_config
        or {
            "platform_name": "Android",
            "device_name": "emulator-5554",
            "app_path": "/opt/testagent/app.apk",
        },
    )


def _make_mock_microvm_sandbox() -> MagicMock:
    sandbox = MagicMock(spec=MicroVMSandbox)
    sandbox.create = AsyncMock(return_value="vm-test-001")
    sandbox.execute = AsyncMock(
        return_value={
            "exit_code": 0,
            "stdout": json.dumps(
                {
                    "status": "passed",
                    "assertion_results": {"executed": {"passed": True, "info": "No assertions defined"}},
                    "logs": "[]",
                    "artifacts": {"screenshots": []},
                }
            ),
            "stderr": "",
        }
    )
    sandbox.get_logs = AsyncMock(return_value="vm log output")
    sandbox.get_artifacts = AsyncMock(return_value=[])
    sandbox.get_tmpdir = AsyncMock(return_value=tempfile.mkdtemp(prefix="testagent-appium-itest-"))
    sandbox.destroy = AsyncMock()
    return sandbox


def _make_appium_test_script(actions: list[dict[str, object]] | None = None) -> str:
    if actions is None:
        actions = [{"action": "launch_app"}]
    return json.dumps({"actions": actions})


class TestAppiumRunnerMicroVMIntegration:
    async def test_runner_setup_in_microvm_mode(self) -> None:
        mock_sandbox = _make_mock_microvm_sandbox()
        runner = AppiumRunner()

        await runner.setup(
            {
                "platform_name": "Android",
                "device_name": "emulator-5554",
                "app_path": "/opt/testagent/app.apk",
            },
            sandbox=mock_sandbox,
            sandbox_id="vm-test-001",
        )

        assert runner._driver is None
        assert runner._sandbox is mock_sandbox
        assert runner._in_docker_mode is True

    async def test_runner_execute_in_microvm_mode(self) -> None:
        mock_sandbox = _make_mock_microvm_sandbox()
        runner = AppiumRunner()

        await runner.setup(
            {
                "platform_name": "Android",
                "device_name": "emulator-5554",
                "app_path": "/opt/testagent/app.apk",
            },
            sandbox=mock_sandbox,
            sandbox_id="vm-test-001",
        )

        mock_sandbox.execute = AsyncMock(
            return_value={
                "exit_code": 0,
                "stdout": json.dumps(
                    {
                        "status": "passed",
                        "assertion_results": {"executed": {"passed": True}},
                        "logs": json.dumps([{"action": "launch_app", "index": 0}]),
                        "artifacts": {"screenshots": []},
                    }
                ),
                "stderr": "",
            }
        )

        with (
            patch.object(runner, "_write_script", AsyncMock(return_value="/tmp/testagent/test_script.py")),
            patch.object(runner, "_run_in_sandbox", AsyncMock(return_value=mock_sandbox.execute.return_value)),
        ):
            result = await runner.execute(_make_appium_test_script())

        assert result.status == "passed"

    async def test_runner_full_lifecycle_microvm(self) -> None:
        mock_sandbox = _make_mock_microvm_sandbox()
        runner = AppiumRunner()

        await runner.setup(
            {
                "platform_name": "Android",
                "device_name": "emulator-5554",
                "app_path": "/opt/testagent/app.apk",
            },
            sandbox=mock_sandbox,
            sandbox_id="vm-test-001",
        )

        with (
            patch.object(runner, "_write_script", AsyncMock(return_value="/tmp/testagent/test_script.py")),
            patch.object(
                runner,
                "_run_in_sandbox",
                AsyncMock(
                    return_value={
                        "exit_code": 0,
                        "stdout": json.dumps(
                            {
                                "status": "passed",
                                "assertion_results": {},
                                "logs": "[]",
                                "artifacts": {"screenshots": []},
                            }
                        ),
                        "stderr": "",
                    }
                ),
            ),
        ):
            result = await runner.execute(_make_appium_test_script())
            collected = await runner.collect_results()

        await runner.teardown()

        assert result.status == "passed"
        assert collected.status == "passed"

    async def test_runner_failed_assertion_in_microvm(self) -> None:
        mock_sandbox = _make_mock_microvm_sandbox()
        runner = AppiumRunner()

        await runner.setup(
            {
                "platform_name": "Android",
                "device_name": "emulator-5554",
                "app_path": "/opt/testagent/app.apk",
            },
            sandbox=mock_sandbox,
            sandbox_id="vm-test-001",
        )

        with (
            patch.object(runner, "_write_script", AsyncMock(return_value="/tmp/testagent/test_script.py")),
            patch.object(
                runner,
                "_run_in_sandbox",
                AsyncMock(
                    return_value={
                        "exit_code": 0,
                        "stdout": json.dumps(
                            {
                                "status": "failed",
                                "assertion_results": {
                                    "assert_text_0": {"passed": False, "actual": "Wrong", "expected": "Hello"}
                                },
                                "logs": "assertion log",
                                "artifacts": {"screenshots": []},
                            }
                        ),
                        "stderr": "",
                    }
                ),
            ),
        ):
            result = await runner.execute(_make_appium_test_script())

        assert result.status == "failed"
        assert result.assertion_results["assert_text_0"]["passed"] is False


class TestOrchestratorAppTestIntegration:
    async def test_dispatch_app_test_uses_microvm(self) -> None:
        task = _make_app_task()
        orchestrator = HarnessOrchestrator()
        level = orchestrator.decide_isolation(task)
        assert level == IsolationLevel.MICROVM

    async def test_dispatch_full_lifecycle_with_mock_sandbox(self) -> None:
        mock_sandbox = _make_mock_microvm_sandbox()
        mock_runner = MagicMock(spec=AppiumRunner)
        mock_runner.setup = AsyncMock()
        mock_runner.execute = AsyncMock(
            return_value=MagicMock(
                status="passed",
                duration_ms=1500.0,
                assertion_results={"executed": {"passed": True}},
                logs="test log",
                artifacts={},
            )
        )
        mock_runner.collect_results = AsyncMock(
            return_value=MagicMock(
                status="passed",
                duration_ms=1500.0,
                assertion_results={"executed": {"passed": True}},
                logs="test log",
                artifacts={},
            )
        )
        mock_runner.teardown = AsyncMock()

        class MockSandboxFactory:
            @classmethod
            def create(cls, level: IsolationLevel | str) -> object:
                return mock_sandbox

            @staticmethod
            def decide_isolation(task_type: str, *, force_local: bool = False) -> IsolationLevel:
                return IsolationLevel.MICROVM

        class MockRunnerFactory:
            @classmethod
            def get_runner(cls, task_type: str) -> AppiumRunner:
                return mock_runner

        orchestrator = HarnessOrchestrator(
            sandbox_factory=MockSandboxFactory,
            runner_factory=MockRunnerFactory,
        )

        task = _make_app_task()
        await orchestrator.dispatch(task)

        mock_sandbox.create.assert_called_once()
        mock_runner.setup.assert_called_once()
        mock_runner.execute.assert_called_once()
        mock_runner.collect_results.assert_called_once()
        mock_runner.teardown.assert_called_once()
        mock_sandbox.destroy.assert_called_once()

    async def test_dispatch_with_retry_app_test(self) -> None:
        mock_sandbox = _make_mock_microvm_sandbox()
        call_count = 0

        mock_result = MagicMock(
            status="passed",
            duration_ms=100.0,
            assertion_results={},
            logs="",
            artifacts={},
        )

        async def _mock_execute(script: str) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Transient failure")
            return mock_result

        mock_runner = MagicMock(spec=AppiumRunner)
        mock_runner.setup = AsyncMock()
        mock_runner.execute = _mock_execute
        mock_runner.collect_results = AsyncMock(return_value=mock_result)
        mock_runner.teardown = AsyncMock()

        class MockSandboxFactory:
            @classmethod
            def create(cls, level: IsolationLevel | str) -> object:
                return mock_sandbox

            @staticmethod
            def decide_isolation(task_type: str, *, force_local: bool = False) -> IsolationLevel:
                return IsolationLevel.MICROVM

        class MockRunnerFactory:
            @classmethod
            def get_runner(cls, task_type: str) -> AppiumRunner:
                return mock_runner

        orchestrator = HarnessOrchestrator(
            sandbox_factory=MockSandboxFactory,
            runner_factory=MockRunnerFactory,
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            await orchestrator.dispatch_with_retry(_make_app_task())

        assert call_count == 2


class TestAppiumRunnerTimeoutProtection:
    async def test_app_test_hard_timeout_is_180s(self) -> None:
        profile = RESOURCE_PROFILES["app_test"]
        assert profile.timeout == 180

    async def test_microvm_sandbox_timeout_matches_app_test(self) -> None:
        config = MicroVMSandbox.SECURITY_CONFIG["app_test"]
        assert config["timeout"] == 180

    async def test_execute_microvm_respects_timeout(self) -> None:
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

    async def test_microvm_timeout_triggers_sandbox_timeout_error(self) -> None:
        sb = MicroVMSandbox()
        sb._vms["vm-timeout"] = {
            "socket_path": "/tmp/fc.sock",
            "task_type": "app_test",
            "process": MagicMock(spec=asyncio.subprocess.Process),
            "log_path": "/tmp/fc.log",
            "work_dir": "/tmp/testagent-vm-test",
            "rootfs_path": "/tmp/rootfs.img",
            "config_path": "/tmp/vm_config.json",
            "tap_device": "",
            "created": True,
        }

        async def _timeout_wait(coro: object, timeout: float) -> None:
            if hasattr(coro, "__await__"):
                await coro
            raise TimeoutError

        destroy_mock = AsyncMock()
        with (
            patch.object(sb, "_send_api_request", AsyncMock()),
            patch("asyncio.wait_for", _timeout_wait),
            patch.object(sb, "destroy", destroy_mock),
            pytest.raises(SandboxTimeoutError) as excinfo,
        ):
            await sb.execute("vm-timeout", "run_appium_test", timeout=180)

        assert excinfo.value.code == "EXECUTION_TIMEOUT"
        destroy_mock.assert_called_once_with("vm-timeout")


class TestAppiumMicroVMADR004Compliance:
    def test_app_test_isolation_is_microvm_not_docker(self) -> None:
        level = SandboxFactory.decide_isolation("app_test")
        assert level == IsolationLevel.MICROVM
        assert level != IsolationLevel.DOCKER

    def test_app_test_resource_quota_4cpu_4gb(self) -> None:
        profile = RESOURCE_PROFILES["app_test"]
        assert profile.cpus == 4
        assert profile.mem_limit == "4g"

    def test_app_test_hard_timeout_180s(self) -> None:
        profile = RESOURCE_PROFILES["app_test"]
        assert profile.timeout == 180

    def test_microvm_security_config_matches_profile(self) -> None:
        profile = RESOURCE_PROFILES["app_test"]
        sec = MicroVMSandbox.SECURITY_CONFIG["app_test"]
        assert sec["vcpu_count"] == profile.cpus
        assert sec["mem_limit_mib"] == 4096
        assert sec["timeout"] == profile.timeout

    def test_local_process_not_allowed_for_app_test(self) -> None:
        level = SandboxFactory.decide_isolation("app_test")
        assert level != IsolationLevel.LOCAL

    def test_force_local_overrides_microvm(self) -> None:
        level = SandboxFactory.decide_isolation("app_test", force_local=True)
        assert level == IsolationLevel.LOCAL

    def test_microvm_sandbox_conforms_to_isandbox(self) -> None:
        assert isinstance(MicroVMSandbox(), ISandbox)

    def test_appium_runner_conforms_to_irunner(self) -> None:
        from testagent.harness.runners.base import IRunner

        assert isinstance(AppiumRunner(), IRunner)

    def test_appium_runner_type_is_app_test(self) -> None:
        assert AppiumRunner.runner_type == "app_test"

    def test_orchestrator_resolves_app_test_to_microvm(self) -> None:
        task = _make_app_task(isolation_level="")
        orchestrator = HarnessOrchestrator()
        level = orchestrator.decide_isolation(task)
        assert level == IsolationLevel.MICROVM


class TestMicroVMAppiumServerStartup:
    @requires_kvm
    async def test_microvm_creates_with_app_test_config(self) -> None:
        sb = MicroVMSandbox()
        with (
            patch("os.path.isfile", return_value=True),
            patch("shutil.copy2"),
            patch("asyncio.create_subprocess_exec") as mock_exec,
        ):
            mock_proc = MagicMock(spec=asyncio.subprocess.Process)
            mock_proc.returncode = None
            mock_proc.pid = 12345
            mock_exec.return_value = mock_proc

            async def _wait_timeout(coro: object, timeout: float) -> None:
                if hasattr(coro, "__await__"):
                    await coro
                raise TimeoutError

            with (
                patch("asyncio.wait_for", _wait_timeout),
                patch("builtins.open", MagicMock()),
            ):
                vm_id = await sb.create({"task_type": "app_test"})

            assert vm_id.startswith("vm-")
            assert sb._vms[vm_id]["task_type"] == "app_test"

            await sb.destroy(vm_id)

    async def test_microvm_appium_server_url_in_script(self) -> None:
        script = AppiumRunner._build_microvm_script(
            actions_json="[]",
            platform_name="Android",
            device_name="emulator-5554",
            app_path="/app.apk",
            automation_name="UiAutomator2",
            appium_server_url="http://127.0.0.1:4723",
            capabilities={},
        )
        assert "http://127.0.0.1:4723" in script
        assert "AppiumWebDriver" in script
        assert "4723" in script

    async def test_microvm_android_capabilities_in_script(self) -> None:
        script = AppiumRunner._build_microvm_script(
            actions_json='[{"action": "launch_app"}]',
            platform_name="Android",
            device_name="Pixel_6",
            app_path="/data/app/test.apk",
            automation_name="UiAutomator2",
            appium_server_url="http://127.0.0.1:4723",
            capabilities={"noReset": True},
        )
        assert "Android" in script
        assert "Pixel_6" in script
        assert "UiAutomator2" in script
        assert "noReset" in script

    async def test_microvm_ios_capabilities_in_script(self) -> None:
        script = AppiumRunner._build_microvm_script(
            actions_json='[{"action": "launch_app"}]',
            platform_name="iOS",
            device_name="iPhone 15",
            app_path="/apps/test.ipa",
            automation_name="XCUITest",
            appium_server_url="http://127.0.0.1:4723",
            capabilities={},
        )
        assert "iOS" in script
        assert "iPhone 15" in script
        assert "XCUITest" in script
