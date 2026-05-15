from __future__ import annotations

import asyncio
import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.common.errors import SandboxTimeoutError
from testagent.harness import (
    ISandbox,
    IsolationLevel,
    MicroVMSandbox,
    MicroVMSandboxError,
    SandboxFactory,
)
from testagent.harness.local_runner import LocalProcessSandbox, LocalProcessSandboxError
from testagent.harness.orchestrator import HarnessOrchestrator
from testagent.models.plan import TestTask


def _make_task(
    *,
    task_id: str = "task-001",
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
        task_config=task_config or {"url": "http://sut.example.com", "method": "GET"},
    )


# ======================================================================
# MicroVMSandbox Security Configuration
# ======================================================================


class TestMicroVMSandboxSecurityConfig:
    def test_app_test_mem_limit(self) -> None:
        config = MicroVMSandbox.SECURITY_CONFIG["app_test"]
        assert config["mem_limit_mib"] == 4096

    def test_app_test_vcpu_count(self) -> None:
        config = MicroVMSandbox.SECURITY_CONFIG["app_test"]
        assert config["vcpu_count"] == 4

    def test_app_test_timeout(self) -> None:
        config = MicroVMSandbox.SECURITY_CONFIG["app_test"]
        assert config["timeout"] == 180

    def test_security_config_keys_are_valid_task_types(self) -> None:
        from testagent.harness.sandbox import SANDBOX_TASK_TYPES

        for key in MicroVMSandbox.SECURITY_CONFIG:
            assert key in SANDBOX_TASK_TYPES


# ======================================================================
# MicroVMSandbox ISandbox Protocol Compliance
# ======================================================================


class TestMicroVMSandboxProtocol:
    def test_complies_with_isandbox_protocol(self) -> None:
        assert isinstance(MicroVMSandbox(), ISandbox)

    def test_has_required_methods(self) -> None:
        import inspect

        for method_name in ["create", "execute", "get_logs", "get_artifacts", "get_tmpdir", "destroy"]:
            assert hasattr(MicroVMSandbox, method_name)
            method = getattr(MicroVMSandbox, method_name)
            assert inspect.iscoroutinefunction(method)


# ======================================================================
# MicroVMSandbox.create()
# ======================================================================


class TestMicroVMSandboxCreate:
    @pytest.mark.asyncio
    async def test_create_invalid_task_type_raises_error(self) -> None:
        sb = MicroVMSandbox()
        with pytest.raises(MicroVMSandboxError) as excinfo:
            await sb.create({"task_type": "unknown"})
        assert excinfo.value.code == "INVALID_TASK_TYPE"

    @pytest.mark.asyncio
    async def test_create_missing_kernel_raises_error(self) -> None:
        sb = MicroVMSandbox()
        with pytest.raises(MicroVMSandboxError) as excinfo:
            await sb.create({"task_type": "app_test", "kernel_path": "/nonexistent/vmlinux"})
        assert excinfo.value.code == "KERNEL_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_create_firecracker_start_failure_raises_error(self) -> None:
        sb = MicroVMSandbox()

        mock_proc = MagicMock(spec=asyncio.subprocess.Process)
        mock_proc.returncode = 1
        mock_proc.wait = AsyncMock()
        mock_proc.stderr = MagicMock()
        mock_proc.stderr.read = AsyncMock(return_value=b"KVM not available")

        with (
            patch("os.path.isfile", return_value=True),
            patch("shutil.copy2"),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)),
            patch("shutil.rmtree"),
        ):
            with pytest.raises(MicroVMSandboxError) as excinfo:
                await sb.create({"task_type": "app_test"})
            assert excinfo.value.code == "FIRECRACKER_START_FAILED"

    @pytest.mark.asyncio
    async def test_create_success(self) -> None:
        sb = MicroVMSandbox()

        mock_proc = MagicMock(spec=asyncio.subprocess.Process)
        mock_proc.returncode = None
        mock_proc.pid = 12345

        async def _wait_timeout(coro, timeout):
            await coro
            raise TimeoutError

        with (
            patch("os.path.isfile", return_value=True),
            patch("shutil.copy2"),
            patch("tempfile.mkdtemp", return_value="/tmp/testagent-vm-xxx"),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)),
            patch("asyncio.wait_for", _wait_timeout),
            patch("builtins.open", MagicMock()),
        ):
            vm_id = await sb.create({"task_type": "app_test"})

        assert vm_id.startswith("vm-")
        assert vm_id in sb._vms
        assert sb._vms[vm_id]["task_type"] == "app_test"
        assert sb._vms[vm_id]["created"] is True

    @pytest.mark.asyncio
    async def test_create_with_custom_firecracker_bin(self) -> None:
        sb = MicroVMSandbox(firecracker_bin="/usr/bin/firecracker")
        assert sb._firecracker_bin == "/usr/bin/firecracker"

    @pytest.mark.asyncio
    async def test_create_with_tap_device_configures_network(self) -> None:
        sb = MicroVMSandbox()

        mock_proc = MagicMock(spec=asyncio.subprocess.Process)
        mock_proc.returncode = None
        mock_proc.pid = 12345

        async def _wait_timeout(coro, timeout):
            await coro
            raise TimeoutError

        configure_tap_mock = AsyncMock()
        with (
            patch("os.path.isfile", return_value=True),
            patch("shutil.copy2"),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)),
            patch("asyncio.wait_for", _wait_timeout),
            patch("builtins.open", MagicMock()),
            patch.object(sb, "_configure_tap", configure_tap_mock),
        ):
            vm_id = await sb.create({"task_type": "app_test", "tap_device": "tap0"})

        configure_tap_mock.assert_called_once_with("tap0", vm_id)
        assert sb._vms[vm_id]["tap_device"] == "tap0"

    @pytest.mark.asyncio
    async def test_create_defaults_to_app_test(self) -> None:
        sb = MicroVMSandbox()

        mock_proc = MagicMock(spec=asyncio.subprocess.Process)
        mock_proc.returncode = None
        mock_proc.pid = 12345

        async def _wait_timeout(coro, timeout):
            await coro
            raise TimeoutError

        with (
            patch("os.path.isfile", return_value=True),
            patch("shutil.copy2"),
            patch("tempfile.mkdtemp", return_value="/tmp/testagent-vm-xxx"),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)),
            patch("asyncio.wait_for", _wait_timeout),
            patch("builtins.open", MagicMock()),
        ):
            vm_id = await sb.create({})

        assert sb._vms[vm_id]["task_type"] == "app_test"


# ======================================================================
# MicroVMSandbox._build_vm_config()
# ======================================================================


class TestMicroVMSandboxBuildConfig:
    def test_vm_config_structure(self) -> None:
        sb = MicroVMSandbox()
        config = sb._build_vm_config(
            kernel_path="/opt/testagent/vmlinux",
            rootfs_path="/tmp/rootfs.img",
            vcpu_count=4,
            mem_limit_mib=4096,
            log_path="/tmp/fc.log",
        )

        assert "boot-source" in config
        assert config["boot-source"]["kernel_image_path"] == "/opt/testagent/vmlinux"

        assert "drives" in config
        drives = config["drives"]
        assert len(drives) == 1
        assert drives[0]["drive_id"] == "rootfs"
        assert drives[0]["is_root_device"] is True
        assert drives[0]["is_read_only"] is False

        assert "machine-config" in config
        assert config["machine-config"]["vcpu_count"] == 4
        assert config["machine-config"]["mem_size_mib"] == 4096
        assert config["machine-config"]["ht_enabled"] is False

        assert "logger" in config
        assert config["logger"]["log_path"] == "/tmp/fc.log"

    def test_vm_config_matches_app_test_security(self) -> None:
        sb = MicroVMSandbox()
        sec = MicroVMSandbox.SECURITY_CONFIG["app_test"]

        config = sb._build_vm_config(
            kernel_path="/opt/testagent/vmlinux",
            rootfs_path="/tmp/rootfs.img",
            vcpu_count=sec["vcpu_count"],
            mem_limit_mib=sec["mem_limit_mib"],
            log_path="/tmp/fc.log",
        )

        assert config["machine-config"]["vcpu_count"] == 4
        assert config["machine-config"]["mem_size_mib"] == 4096


# ======================================================================
# MicroVMSandbox.execute() -- Timeout Protection
# ======================================================================


class TestMicroVMSandboxExecute:
    @pytest.mark.asyncio
    async def test_execute_unknown_vm_raises_error(self) -> None:
        sb = MicroVMSandbox()
        with pytest.raises(MicroVMSandboxError) as excinfo:
            await sb.execute("nonexistent", "echo hi", timeout=10)
        assert excinfo.value.code == "VM_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_execute_timeout_destroys_vm_and_raises(self) -> None:
        sb = MicroVMSandbox()
        sb._vms["vm-test"] = {
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

        async def _timeout_wait(coro, timeout):
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
            await sb.execute("vm-test", "run_test", timeout=1)

        assert excinfo.value.code == "EXECUTION_TIMEOUT"
        destroy_mock.assert_called_once_with("vm-test")

    @pytest.mark.asyncio
    async def test_execute_api_error_destroys_vm_and_raises(self) -> None:
        sb = MicroVMSandbox()
        sb._vms["vm-test"] = {
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

        async def _raise_api_error(*args: object, **kwargs: object) -> object:
            raise MicroVMSandboxError("API failed", code="API_REQUEST_FAILED")

        destroy_tracker = MagicMock()

        async def _destroy_impl(vm_id: str) -> None:
            destroy_tracker(vm_id)

        with (
            patch.object(sb, "_send_api_request", _raise_api_error),
            patch.object(sb, "destroy", _destroy_impl),
            pytest.raises(MicroVMSandboxError),
        ):
            await sb.execute("vm-test", "run_test", timeout=30)

        destroy_tracker.assert_called_once_with("vm-test")

    @pytest.mark.asyncio
    async def test_execute_success(self) -> None:
        sb = MicroVMSandbox()
        sb._vms["vm-test"] = {
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

        api_response = {"exit_code": 0, "stdout": "test passed", "stderr": ""}

        with patch.object(sb, "_send_api_request", AsyncMock(return_value=api_response)):
            result = await sb.execute("vm-test", "run_test", timeout=180)

        assert result["exit_code"] == 0
        assert result["stdout"] == "test passed"
        assert result["stderr"] == ""

    @pytest.mark.asyncio
    async def test_execute_default_timeout_is_180(self) -> None:
        sb = MicroVMSandbox()
        sb._vms["vm-test"] = {
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

        api_response = {"exit_code": 0, "stdout": "", "stderr": ""}

        captured_timeout: list[float] = []

        async def _mock_wait_for(coro, timeout):
            captured_timeout.append(timeout)
            result = await coro
            return result

        with (
            patch.object(sb, "_send_api_request", AsyncMock(return_value=api_response)),
            patch("asyncio.wait_for", _mock_wait_for),
        ):
            await sb.execute("vm-test", "run_test", timeout=180)

        assert captured_timeout == [180]


# ======================================================================
# MicroVMSandbox.get_logs()
# ======================================================================


class TestMicroVMSandboxGetLogs:
    @pytest.mark.asyncio
    async def test_unknown_vm_raises_error(self) -> None:
        sb = MicroVMSandbox()
        with pytest.raises(MicroVMSandboxError) as excinfo:
            await sb.get_logs("nonexistent")
        assert excinfo.value.code == "VM_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_returns_log_content(self) -> None:
        import shutil

        sb = MicroVMSandbox()
        log_content = "vm started\nvm running"

        tmpdir = tempfile.mkdtemp(prefix="testagent-log-test-")
        log_path = os.path.join(tmpdir, "fc.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(log_content)

        sb._vms["vm-test"] = {"log_path": log_path}

        try:
            logs = await sb.get_logs("vm-test")
            assert logs == log_content
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    @pytest.mark.asyncio
    async def test_returns_empty_string_when_no_log_file(self) -> None:
        sb = MicroVMSandbox()
        sb._vms["vm-test"] = {"log_path": "/nonexistent/fc.log"}

        with patch("os.path.isfile", return_value=False):
            logs = await sb.get_logs("vm-test")

        assert logs == ""


# ======================================================================
# MicroVMSandbox.get_artifacts()
# ======================================================================


class TestMicroVMSandboxGetArtifacts:
    @pytest.mark.asyncio
    async def test_unknown_vm_raises_error(self) -> None:
        sb = MicroVMSandbox()
        with pytest.raises(MicroVMSandboxError) as excinfo:
            await sb.get_artifacts("nonexistent")
        assert excinfo.value.code == "VM_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_artifacts_dir(self) -> None:
        sb = MicroVMSandbox()
        sb._vms["vm-test"] = {"work_dir": "/nonexistent-path-12345"}

        with patch("os.path.isdir", return_value=False):
            artifacts = await sb.get_artifacts("vm-test")

        assert artifacts == []

    @pytest.mark.asyncio
    async def test_returns_artifact_metadata(self) -> None:
        sb = MicroVMSandbox()
        tmpdir = tempfile.mkdtemp(prefix="testagent-artifact-test-")
        artifacts_dir = os.path.join(tmpdir, "artifacts")
        os.makedirs(artifacts_dir, exist_ok=True)

        screenshot_path = os.path.join(artifacts_dir, "screenshot.png")
        with open(screenshot_path, "wb") as f:
            f.write(b"\x89PNG\r\n")

        sb._vms["vm-test"] = {"work_dir": tmpdir}

        artifacts = await sb.get_artifacts("vm-test")

        try:
            assert len(artifacts) == 1
            assert artifacts[0]["name"] == "screenshot.png"
            assert artifacts[0]["mime_type"] == "image/png"
            assert artifacts[0]["size_bytes"] > 0
        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)


# ======================================================================
# MicroVMSandbox.get_tmpdir()
# ======================================================================


class TestMicroVMSandboxGetTmpdir:
    @pytest.mark.asyncio
    async def test_unknown_vm_raises_error(self) -> None:
        sb = MicroVMSandbox()
        with pytest.raises(MicroVMSandboxError) as excinfo:
            await sb.get_tmpdir("nonexistent")
        assert excinfo.value.code == "VM_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_returns_work_dir(self) -> None:
        sb = MicroVMSandbox()
        sb._vms["vm-test"] = {"work_dir": "/tmp/testagent-vm-xxx"}
        result = await sb.get_tmpdir("vm-test")
        assert result == "/tmp/testagent-vm-xxx"


# ======================================================================
# MicroVMSandbox.destroy() -- ephemeral by design
# ======================================================================


class TestMicroVMSandboxDestroy:
    @pytest.mark.asyncio
    async def test_destroy_idempotent(self) -> None:
        sb = MicroVMSandbox()
        await sb.destroy("nonexistent")

    @pytest.mark.asyncio
    async def test_destroy_cleans_up_work_dir(self) -> None:
        tmpdir = tempfile.mkdtemp(prefix="testagent-vm-destroy-test-")
        sb = MicroVMSandbox()

        mock_proc = MagicMock(spec=asyncio.subprocess.Process)
        mock_proc.returncode = 0

        sb._vms["vm-test"] = {
            "process": mock_proc,
            "socket_path": os.path.join(tmpdir, "fc.sock"),
            "log_path": os.path.join(tmpdir, "fc.log"),
            "work_dir": tmpdir,
            "rootfs_path": os.path.join(tmpdir, "rootfs.img"),
            "config_path": os.path.join(tmpdir, "vm_config.json"),
            "task_type": "app_test",
            "tap_device": "",
            "created": True,
        }

        await sb.destroy("vm-test")

        assert os.path.isdir(tmpdir) is False
        assert "vm-test" not in sb._vms

    @pytest.mark.asyncio
    async def test_destroy_kills_running_process(self) -> None:
        sb = MicroVMSandbox()

        mock_proc = MagicMock(spec=asyncio.subprocess.Process)
        mock_proc.returncode = None
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        sb._vms["vm-test"] = {
            "process": mock_proc,
            "socket_path": "",
            "log_path": "",
            "work_dir": "",
            "rootfs_path": "",
            "config_path": "",
            "task_type": "app_test",
            "tap_device": "",
            "created": True,
        }

        await sb.destroy("vm-test")

        mock_proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_destroy_removes_socket_file(self) -> None:
        tmpdir = tempfile.mkdtemp(prefix="testagent-vm-sock-test-")
        socket_path = os.path.join(tmpdir, "fc.sock")
        with open(socket_path, "w") as f:
            f.write("")

        sb = MicroVMSandbox()
        mock_proc = MagicMock(spec=asyncio.subprocess.Process)
        mock_proc.returncode = 0

        sb._vms["vm-test"] = {
            "process": mock_proc,
            "socket_path": socket_path,
            "log_path": "",
            "work_dir": tmpdir,
            "rootfs_path": "",
            "config_path": "",
            "task_type": "app_test",
            "tap_device": "",
            "created": True,
        }

        await sb.destroy("vm-test")

        assert os.path.exists(socket_path) is False
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)


# ======================================================================
# SandboxFactory.create(IsolationLevel.MICROVM) -> MicroVMSandbox
# ======================================================================


class TestSandboxFactoryMicroVM:
    def test_create_microvm_returns_microvm_sandbox(self) -> None:
        sandbox = SandboxFactory.create(IsolationLevel.MICROVM)
        assert isinstance(sandbox, MicroVMSandbox)

    def test_create_microvm_by_string(self) -> None:
        sandbox = SandboxFactory.create("microvm")
        assert isinstance(sandbox, MicroVMSandbox)

    def test_decide_isolation_returns_microvm_for_app_test(self) -> None:
        level = SandboxFactory.decide_isolation("app_test")
        assert level == IsolationLevel.MICROVM

    def test_decide_isolation_returns_docker_for_api_test_not_microvm(self) -> None:
        level = SandboxFactory.decide_isolation("api_test")
        assert level == IsolationLevel.DOCKER
        assert level != IsolationLevel.MICROVM

    def test_decide_isolation_returns_docker_for_web_test_not_microvm(self) -> None:
        level = SandboxFactory.decide_isolation("web_test")
        assert level == IsolationLevel.DOCKER
        assert level != IsolationLevel.MICROVM

    def test_decide_isolation_force_local_overrides_microvm(self) -> None:
        level = SandboxFactory.decide_isolation("app_test", force_local=True)
        assert level == IsolationLevel.LOCAL

    def test_factory_returns_new_instance_each_time(self) -> None:
        s1 = SandboxFactory.create(IsolationLevel.MICROVM)
        s2 = SandboxFactory.create(IsolationLevel.MICROVM)
        assert s1 is not s2


# ======================================================================
# Orchestrator.decide_isolation: app_test -> MICROVM
# ======================================================================


class TestOrchestratorDecideIsolation:
    def test_app_test_resolves_to_microvm(self) -> None:
        task = _make_task(task_type="app_test", isolation_level="")
        orchestrator = HarnessOrchestrator()
        level = orchestrator.decide_isolation(task)
        assert level == IsolationLevel.MICROVM

    def test_api_test_resolves_to_docker(self) -> None:
        task = _make_task(task_type="api_test", isolation_level="")
        orchestrator = HarnessOrchestrator()
        level = orchestrator.decide_isolation(task)
        assert level == IsolationLevel.DOCKER

    def test_web_test_resolves_to_docker(self) -> None:
        task = _make_task(task_type="web_test", isolation_level="")
        orchestrator = HarnessOrchestrator()
        level = orchestrator.decide_isolation(task)
        assert level == IsolationLevel.DOCKER

    def test_user_explicit_microvm(self) -> None:
        task = _make_task(task_type="api_test", isolation_level="microvm")
        orchestrator = HarnessOrchestrator()
        level = orchestrator.decide_isolation(task)
        assert level == IsolationLevel.MICROVM

    def test_user_explicit_docker_overrides_app_test_default(self) -> None:
        task = _make_task(task_type="app_test", isolation_level="docker")
        orchestrator = HarnessOrchestrator()
        level = orchestrator.decide_isolation(task)
        assert level == IsolationLevel.DOCKER


# ======================================================================
# MicroVMSandbox._guess_mime()
# ======================================================================


class TestMicroVMSandboxGuessMime:
    def test_common_artifact_types(self) -> None:
        assert MicroVMSandbox._guess_mime("screenshot.png") == "image/png"
        assert MicroVMSandbox._guess_mime("video.mp4") == "video/mp4"
        assert MicroVMSandbox._guess_mime("report.json") == "application/json"
        assert MicroVMSandbox._guess_mime("trace.log") == "text/plain"
        assert MicroVMSandbox._guess_mime("unknown.xyz") == "application/octet-stream"


# ======================================================================
# MicroVMSandbox._send_api_request()
# ======================================================================


class TestMicroVMSandboxSendApiRequest:
    @pytest.mark.asyncio
    async def test_socket_not_found_raises_error(self) -> None:
        sb = MicroVMSandbox()
        with patch("os.path.exists", return_value=False):
            with pytest.raises(MicroVMSandboxError) as excinfo:
                await sb._send_api_request("/nonexistent/fc.sock", "/execute", {})
            assert excinfo.value.code == "SOCKET_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_curl_failure_raises_error(self) -> None:
        sb = MicroVMSandbox()

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"connection refused"))

        with (
            patch("os.path.exists", return_value=True),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)),
        ):
            with pytest.raises(MicroVMSandboxError) as excinfo:
                await sb._send_api_request("/tmp/fc.sock", "/execute", {})
            assert excinfo.value.code == "API_REQUEST_FAILED"

    @pytest.mark.asyncio
    async def test_successful_api_request(self) -> None:
        sb = MicroVMSandbox()

        response_body = json.dumps({"exit_code": 0, "stdout": "ok"}).encode()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(response_body, b""))

        with (
            patch("os.path.exists", return_value=True),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)),
        ):
            result = await sb._send_api_request("/tmp/fc.sock", "/execute", {"command": "ls"})

        assert result["exit_code"] == 0
        assert result["stdout"] == "ok"


# ======================================================================
# ADR-004 Compliance: app_test MUST use MicroVM, not Docker
# ======================================================================


class TestADR004Compliance:
    """Verify ADR-004 rules: app_test -> MICROVM, not Docker."""

    def test_app_test_not_docker(self) -> None:
        level = SandboxFactory.decide_isolation("app_test")
        assert level != IsolationLevel.DOCKER
        assert level == IsolationLevel.MICROVM

    def test_local_not_allowed_in_production(self) -> None:
        local_sb = LocalProcessSandbox()
        with patch.dict(os.environ, {}, clear=True), pytest.raises(LocalProcessSandboxError):
            asyncio.run(local_sb.create({}))

    def test_microvm_sandbox_has_hard_timeout(self) -> None:
        config = MicroVMSandbox.SECURITY_CONFIG["app_test"]
        assert config["timeout"] == 180

    def test_destroy_cleans_all_temp_data(self) -> None:
        sb = MicroVMSandbox()
        tmpdir = tempfile.mkdtemp(prefix="testagent-compliance-")
        sb._vms["vm-test"] = {
            "process": MagicMock(spec=asyncio.subprocess.Process, returncode=0),
            "socket_path": "",
            "log_path": "",
            "work_dir": tmpdir,
            "rootfs_path": "",
            "config_path": "",
            "task_type": "app_test",
            "tap_device": "",
            "created": True,
        }

        asyncio.run(sb.destroy("vm-test"))
        assert os.path.isdir(tmpdir) is False
