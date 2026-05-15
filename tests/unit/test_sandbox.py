from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.common.errors import SandboxTimeoutError
from testagent.harness import (
    RESOURCE_PROFILES,
    SANDBOX_TASK_TYPES,
    DockerSandbox,
    DockerSandboxError,
    ISandbox,
    IsolationLevel,
    LocalProcessSandbox,
    LocalProcessSandboxError,
    MicroVMSandbox,
    MicroVMSandboxError,
    ResourceManager,
    ResourceProfile,
    SandboxFactory,
    SandboxFactoryError,
)

# ======================================================================
# ResourceProfile
# ======================================================================


class TestResourceProfile:
    def test_api_profile(self) -> None:
        p = RESOURCE_PROFILES["api_test"]
        assert p.cpus == 1
        assert p.mem_limit == "512m"
        assert p.timeout == 60
        assert p.read_only is True

    def test_web_profile(self) -> None:
        p = RESOURCE_PROFILES["web_test"]
        assert p.cpus == 2
        assert p.mem_limit == "2g"
        assert p.timeout == 120
        assert p.read_only is True

    def test_app_profile(self) -> None:
        p = RESOURCE_PROFILES["app_test"]
        assert p.cpus == 4
        assert p.mem_limit == "4g"
        assert p.timeout == 180
        assert p.read_only is True

    def test_to_dict(self) -> None:
        p = ResourceProfile(cpus=2, mem_limit="1g", timeout=90, read_only=False)
        d = p.to_dict()
        assert d == {"cpus": 2, "mem_limit": "1g", "timeout": 90, "read_only": False}

    def test_sandbox_task_types(self) -> None:
        assert "api_test" in SANDBOX_TASK_TYPES
        assert "web_test" in SANDBOX_TASK_TYPES
        assert "app_test" in SANDBOX_TASK_TYPES
        assert len(SANDBOX_TASK_TYPES) == 3


# ======================================================================
# ISandbox Protocol
# ======================================================================


class TestISandboxProtocol:
    def test_docker_sandbox_complies(self) -> None:
        assert isinstance(DockerSandbox(), ISandbox)

    def test_local_sandbox_complies(self) -> None:
        assert isinstance(LocalProcessSandbox(), ISandbox)

    def test_microvm_sandbox_complies(self) -> None:
        assert isinstance(MicroVMSandbox(), ISandbox)

    def test_protocol_method_signatures(self) -> None:
        import inspect

        for cls in [DockerSandbox, LocalProcessSandbox, MicroVMSandbox]:
            for method_name in ["create", "execute", "destroy"]:
                assert hasattr(cls, method_name), f"{cls.__name__} missing {method_name}"
                method = getattr(cls, method_name)
                assert inspect.iscoroutinefunction(method), f"{cls.__name__}.{method_name} must be async"


# ======================================================================
# SandboxFactory
# ======================================================================


class TestSandboxFactory:
    def test_create_docker(self) -> None:
        sandbox = SandboxFactory.create(IsolationLevel.DOCKER)
        assert isinstance(sandbox, DockerSandbox)

    def test_create_local(self) -> None:
        sandbox = SandboxFactory.create(IsolationLevel.LOCAL)
        assert isinstance(sandbox, LocalProcessSandbox)

    def test_create_microvm(self) -> None:
        sandbox = SandboxFactory.create(IsolationLevel.MICROVM)
        assert isinstance(sandbox, MicroVMSandbox)

    def test_create_unknown_level_raises_error(self) -> None:
        with pytest.raises(SandboxFactoryError) as excinfo:
            SandboxFactory.create("fake")
        assert "UNKNOWN_ISOLATION_LEVEL" in excinfo.value.code

    def test_factory_returns_new_instance_each_time(self) -> None:
        s1 = SandboxFactory.create(IsolationLevel.DOCKER)
        s2 = SandboxFactory.create(IsolationLevel.DOCKER)
        assert s1 is not s2

    def test_decide_isolation_returns_docker_for_api(self) -> None:
        level = SandboxFactory.decide_isolation("api_test")
        assert level == IsolationLevel.DOCKER

    def test_decide_isolation_returns_docker_for_web(self) -> None:
        level = SandboxFactory.decide_isolation("web_test")
        assert level == IsolationLevel.DOCKER

    def test_decide_isolation_returns_microvm_for_app(self) -> None:
        level = SandboxFactory.decide_isolation("app_test")
        assert level == IsolationLevel.MICROVM

    def test_decide_isolation_raises_on_unknown_type(self) -> None:
        with pytest.raises(SandboxFactoryError) as excinfo:
            SandboxFactory.decide_isolation("unknown_type")
        assert "UNKNOWN_TASK_TYPE" in excinfo.value.code

    def test_decide_isolation_force_local(self) -> None:
        level = SandboxFactory.decide_isolation("api_test", force_local=True)
        assert level == IsolationLevel.LOCAL

    def test_decide_isolation_force_local_unknown_type(self) -> None:
        level = SandboxFactory.decide_isolation("unknown_type", force_local=True)
        assert level == IsolationLevel.LOCAL


# ======================================================================
# DockerSandbox
# ======================================================================


class TestDockerSandbox:
    def test_security_opts_are_class_var(self) -> None:
        assert DockerSandbox.SECURITY_OPTS == ["no-new-privileges"]

    def test_network_opts_default_to_none(self) -> None:
        assert DockerSandbox.NETWORK_OPTS == ["--network", "none"]

    @pytest.mark.asyncio
    async def test_create_missing_image_raises_error(self) -> None:
        sb = DockerSandbox()
        with pytest.raises(DockerSandboxError) as excinfo:
            await sb.create({})
        assert "MISSING_IMAGE" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_create_empty_image_raises_error(self) -> None:
        sb = DockerSandbox()
        with pytest.raises(DockerSandboxError) as excinfo:
            await sb.create({"image": ""})
        assert "MISSING_IMAGE" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_create_invalid_task_type_raises_error(self) -> None:
        sb = DockerSandbox()
        with pytest.raises(DockerSandboxError) as excinfo:
            await sb.create({"image": "test:latest", "task_type": "unknown"})
        assert "INVALID_TASK_TYPE" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_create_docker_failure_raises_error(self) -> None:
        sb = DockerSandbox()
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"some error"))

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
            with pytest.raises(DockerSandboxError) as excinfo:
                await sb.create({"image": "test:latest", "task_type": "api_test"})
            assert "DOCKER_CREATE_FAILED" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_create_success(self) -> None:
        sb = DockerSandbox()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"abc123def\n", b""))

        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)),
            patch("tempfile.mkdtemp", return_value="/tmp/testagent-xxx"),
        ):
            sandbox_id = await sb.create({"image": "test:latest", "task_type": "api_test"})

        assert sandbox_id.startswith("sandbox-")
        assert sandbox_id in sb._containers
        assert sb._containers[sandbox_id]["container_id"] == "abc123def"
        assert sb._containers[sandbox_id]["image"] == "test:latest"
        assert sb._containers[sandbox_id]["task_type"] == "api_test"

    @pytest.mark.asyncio
    async def test_execute_unknown_sandbox_raises_error(self) -> None:
        sb = DockerSandbox()
        with pytest.raises(DockerSandboxError) as excinfo:
            await sb.execute("nonexistent", "echo hi", timeout=10)
        assert "SANDBOX_NOT_FOUND" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_execute_success(self) -> None:
        sb = DockerSandbox()
        sb._containers["sandbox-test"] = {
            "container_id": "cid123",
            "task_type": "api_test",
        }

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"hello", b""))

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
            result = await sb.execute("sandbox-test", "echo hello", timeout=10)

        assert result["exit_code"] == 0
        assert result["stdout"] == "hello"
        assert result["stderr"] == ""

    @pytest.mark.asyncio
    async def test_execute_timeout_raises_error(self) -> None:
        sb = DockerSandbox()
        sb._containers["sandbox-test"] = {
            "container_id": "cid123",
            "task_type": "api_test",
        }

        mock_proc = MagicMock()

        async def _timeout_wait(coro, timeout):  # type: ignore[no-untyped-def]
            raise TimeoutError

        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)),
            patch("asyncio.wait_for", _timeout_wait),
            patch.object(sb, "_force_kill", AsyncMock()),
            pytest.raises(SandboxTimeoutError) as excinfo,
        ):
            await sb.execute("sandbox-test", "sleep 999", timeout=1)

        assert "EXECUTION_TIMEOUT" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_get_logs_unknown_sandbox_raises_error(self) -> None:
        sb = DockerSandbox()
        with pytest.raises(DockerSandboxError) as excinfo:
            await sb.get_logs("nonexistent")
        assert "SANDBOX_NOT_FOUND" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_get_logs_success(self) -> None:
        sb = DockerSandbox()
        sb._containers["sandbox-test"] = {
            "container_id": "cid123",
            "task_type": "api_test",
        }

        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(b"log line 1\nlog line 2", b""))

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
            logs = await sb.get_logs("sandbox-test")

        assert "log line 1" in logs
        assert "log line 2" in logs

    @pytest.mark.asyncio
    async def test_get_artifacts_unknown_sandbox_raises_error(self) -> None:
        sb = DockerSandbox()
        with pytest.raises(DockerSandboxError) as excinfo:
            await sb.get_artifacts("nonexistent")
        assert "SANDBOX_NOT_FOUND" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_get_artifacts_empty_when_no_tmpdir(self) -> None:
        sb = DockerSandbox()
        sb._containers["sandbox-test"] = {
            "container_id": "cid123",
            "task_type": "api_test",
        }
        artifacts = await sb.get_artifacts("sandbox-test")
        assert artifacts == []

    @pytest.mark.asyncio
    async def test_destroy_idempotent(self) -> None:
        sb = DockerSandbox()
        await sb.destroy("nonexistent")

    @pytest.mark.asyncio
    async def test_destroy_cleans_up_tmpdir(self) -> None:
        import tempfile

        tmpdir = tempfile.mkdtemp(prefix="testagent-test-")
        sb = DockerSandbox()
        sb._containers["sandbox-test"] = {
            "container_id": "cid123",
            "task_type": "api_test",
            "tmpdir": tmpdir,
        }

        with (
            patch.object(sb, "_force_kill", AsyncMock()),
            patch("asyncio.create_subprocess_exec", AsyncMock()),
        ):
            await sb.destroy("sandbox-test")

        assert os.path.isdir(tmpdir) is False
        assert "sandbox-test" not in sb._containers

    def test_guess_mime(self) -> None:
        assert DockerSandbox._guess_mime("report.json") == "application/json"
        assert DockerSandbox._guess_mime("page.html") == "text/html"
        assert DockerSandbox._guess_mime("screenshot.png") == "image/png"
        assert DockerSandbox._guess_mime("photo.jpeg") == "image/jpeg"
        assert DockerSandbox._guess_mime("data.csv") == "text/csv"
        assert DockerSandbox._guess_mime("archive.zip") == "application/zip"
        assert DockerSandbox._guess_mime("unknown.xyz") == "application/octet-stream"


# ======================================================================
# LocalProcessSandbox
# ======================================================================


class TestLocalProcessSandbox:
    def test_env_guard_not_set_raises_error(self) -> None:
        sb = LocalProcessSandbox()
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(LocalProcessSandboxError) as excinfo:
                import asyncio

                asyncio.run(sb.create({}))
            assert "LOCAL_SANDBOX_FORBIDDEN" in excinfo.value.code

    def test_env_guard_set_creates_sandbox(self) -> None:
        sb = LocalProcessSandbox()
        with patch.dict(os.environ, {"TESTAGENT_ALLOW_LOCAL": "1"}, clear=True):
            import asyncio

            sandbox_id = asyncio.run(sb.create({}))
            assert sandbox_id.startswith("local-")
            assert sandbox_id in sb._sandboxes

    @pytest.mark.asyncio
    async def test_execute_unknown_sandbox_raises_error(self) -> None:
        sb = LocalProcessSandbox()
        with pytest.raises(LocalProcessSandboxError) as excinfo:
            await sb.execute("nonexistent", "echo hi", timeout=10)
        assert "SANDBOX_NOT_FOUND" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_execute_success(self) -> None:
        sb = LocalProcessSandbox()
        sb._sandboxes["local-test"] = {"working_dir": "/tmp"}

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"hello from local", b""))

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
            result = await sb.execute("local-test", "echo hello", timeout=10)

        assert result["exit_code"] == 0
        assert result["stdout"] == "hello from local"

    @pytest.mark.asyncio
    async def test_execute_timeout_raises_error(self) -> None:
        sb = LocalProcessSandbox()
        sb._sandboxes["local-test"] = {"working_dir": "/tmp"}

        mock_proc = MagicMock()
        mock_proc.kill = MagicMock()
        mock_proc.wait = AsyncMock()

        async def _timeout_wait(coro, timeout):  # type: ignore[no-untyped-def]
            raise TimeoutError

        with (
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)),
            patch("asyncio.wait_for", _timeout_wait),
            pytest.raises(SandboxTimeoutError) as excinfo,
        ):
            await sb.execute("local-test", "sleep 999", timeout=1)

        assert "EXECUTION_TIMEOUT" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_get_logs_returns_description(self) -> None:
        sb = LocalProcessSandbox()
        sb._sandboxes["local-test"] = {"working_dir": "/tmp"}
        logs = await sb.get_logs("local-test")
        assert "does not capture persistent logs" in logs

    @pytest.mark.asyncio
    async def test_get_logs_unknown_sandbox_raises_error(self) -> None:
        sb = LocalProcessSandbox()
        with pytest.raises(LocalProcessSandboxError) as excinfo:
            await sb.get_logs("nonexistent")
        assert "SANDBOX_NOT_FOUND" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_get_artifacts_unknown_sandbox_raises_error(self) -> None:
        sb = LocalProcessSandbox()
        with pytest.raises(LocalProcessSandboxError) as excinfo:
            await sb.get_artifacts("nonexistent")
        assert "SANDBOX_NOT_FOUND" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_get_artifacts_empty_when_no_dir(self) -> None:
        sb = LocalProcessSandbox()
        sb._sandboxes["local-test"] = {"working_dir": "/nonexistent-path-12345"}
        artifacts = await sb.get_artifacts("local-test")
        assert artifacts == []

    @pytest.mark.asyncio
    async def test_destroy_idempotent(self) -> None:
        sb = LocalProcessSandbox()
        await sb.destroy("nonexistent")

    @pytest.mark.asyncio
    async def test_destroy_cleans_up_working_dir(self) -> None:
        import tempfile

        tmpdir = tempfile.mkdtemp(prefix="testagent-local-test-")
        sb = LocalProcessSandbox()
        sb._sandboxes["local-test"] = {"working_dir": tmpdir}

        await sb.destroy("local-test")

        assert os.path.isdir(tmpdir) is False
        assert "local-test" not in sb._sandboxes


# ======================================================================
# MicroVMSandbox (V1.0 placeholder)
# ======================================================================


class TestMicroVMSandbox:
    def test_security_config_app_test(self) -> None:
        config = MicroVMSandbox.SECURITY_CONFIG["app_test"]
        assert config["mem_limit_mib"] == 4096
        assert config["vcpu_count"] == 4
        assert config["timeout"] == 180

    @pytest.mark.asyncio
    async def test_create_invalid_task_type_raises_error(self) -> None:
        sb = MicroVMSandbox()
        with pytest.raises(MicroVMSandboxError) as excinfo:
            await sb.create({"task_type": "unknown"})
        assert "INVALID_TASK_TYPE" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_create_missing_kernel_raises_error(self) -> None:
        sb = MicroVMSandbox()
        with pytest.raises(MicroVMSandboxError) as excinfo:
            await sb.create({"task_type": "app_test", "kernel_path": "/nonexistent/vmlinux"})
        assert "KERNEL_NOT_FOUND" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_execute_unknown_vm_raises_error(self) -> None:
        sb = MicroVMSandbox()
        with pytest.raises(MicroVMSandboxError) as excinfo:
            await sb.execute("nonexistent", "echo hi", timeout=10)
        assert "VM_NOT_FOUND" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_get_logs_unknown_vm_raises_error(self) -> None:
        sb = MicroVMSandbox()
        with pytest.raises(MicroVMSandboxError) as excinfo:
            await sb.get_logs("nonexistent")
        assert "VM_NOT_FOUND" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_get_artifacts_unknown_vm_raises_error(self) -> None:
        sb = MicroVMSandbox()
        with pytest.raises(MicroVMSandboxError) as excinfo:
            await sb.get_artifacts("nonexistent")
        assert "VM_NOT_FOUND" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_get_tmpdir_unknown_vm_raises_error(self) -> None:
        sb = MicroVMSandbox()
        with pytest.raises(MicroVMSandboxError) as excinfo:
            await sb.get_tmpdir("nonexistent")
        assert "VM_NOT_FOUND" in excinfo.value.code

    @pytest.mark.asyncio
    async def test_destroy_idempotent(self) -> None:
        sb = MicroVMSandbox()
        await sb.destroy("nonexistent")


# ======================================================================
# ResourceManager
# ======================================================================


class TestResourceManager:
    @pytest.mark.asyncio
    async def test_check_disk_usage_returns_float(self) -> None:
        rm = ResourceManager(docker_data_path="/tmp")
        pct = await rm.check_disk_usage()
        assert isinstance(pct, float)
        assert 0.0 <= pct <= 1.0

    @pytest.mark.asyncio
    async def test_check_disk_usage_nonexistent_path(self) -> None:
        rm = ResourceManager(docker_data_path="/totally-nonexistent-path-12345")
        pct = await rm.check_disk_usage()
        assert pct == 0.0

    @pytest.mark.asyncio
    async def test_should_pause_returns_false_when_below_threshold(self) -> None:
        rm = ResourceManager(docker_data_path="/tmp")
        with patch.object(rm, "check_disk_usage", AsyncMock(return_value=0.3)):
            paused = await rm.should_pause_new_tasks()
        assert paused is False
        assert rm._paused is False

    @pytest.mark.asyncio
    async def test_should_pause_returns_true_when_above_threshold(self) -> None:
        rm = ResourceManager(docker_data_path="/tmp")
        with patch.object(rm, "check_disk_usage", AsyncMock(return_value=0.85)):
            paused = await rm.should_pause_new_tasks()
        assert paused is True
        assert rm._paused is True

    @pytest.mark.asyncio
    async def test_should_pause_tracks_state_transition(self) -> None:
        rm = ResourceManager(docker_data_path="/tmp")
        assert rm._paused is False

        with patch.object(rm, "check_disk_usage", AsyncMock(return_value=0.85)):
            paused1 = await rm.should_pause_new_tasks()
        assert paused1 is True
        assert rm._paused is True

        with patch.object(rm, "check_disk_usage", AsyncMock(return_value=0.90)):
            paused2 = await rm.should_pause_new_tasks()
        assert paused2 is True

        with patch.object(rm, "check_disk_usage", AsyncMock(return_value=0.30)):
            paused3 = await rm.should_pause_new_tasks()
        assert paused3 is False
        assert rm._paused is False

    @pytest.mark.asyncio
    async def test_cleanup_exited_containers_success(self) -> None:
        rm = ResourceManager()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"container1\ncontainer2\n", b""))

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
            count = await rm.cleanup_exited_containers()

        assert count == 2

    @pytest.mark.asyncio
    async def test_cleanup_exited_containers_no_output(self) -> None:
        rm = ResourceManager()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
            count = await rm.cleanup_exited_containers()

        assert count == 0

    @pytest.mark.asyncio
    async def test_cleanup_dangling_images_success(self) -> None:
        rm = ResourceManager()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"sha256:abc\nsha256:def\n", b""))

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)):
            count = await rm.cleanup_dangling_images()

        assert count == 2

    @pytest.mark.asyncio
    async def test_emergency_cleanup_triggers_when_above_threshold(self) -> None:
        rm = ResourceManager()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with (
            patch.object(rm, "check_disk_usage", AsyncMock(return_value=0.95)),
            patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)),
        ):
            await rm.emergency_cleanup()

    @pytest.mark.asyncio
    async def test_emergency_cleanup_skipped_below_threshold(self) -> None:
        rm = ResourceManager()
        with patch.object(rm, "check_disk_usage", AsyncMock(return_value=0.5)):
            await rm.emergency_cleanup()

    @pytest.mark.asyncio
    async def test_periodic_cleanup_start_stop(self) -> None:
        rm = ResourceManager()
        await rm.start_periodic_cleanup()
        assert rm._cleanup_task is not None
        assert not rm._cleanup_task.done()

        await rm.stop_periodic_cleanup()
        assert rm._cleanup_task is None

    @pytest.mark.asyncio
    async def test_periodic_cleanup_idempotent_start(self) -> None:
        rm = ResourceManager()
        await rm.start_periodic_cleanup()
        await rm.start_periodic_cleanup()
        await rm.stop_periodic_cleanup()

    def test_threshold_constants(self) -> None:
        assert ResourceManager.PAUSE_THRESHOLD == 0.80
        assert ResourceManager.EMERGENCY_THRESHOLD == 0.90
        assert ResourceManager.CHECK_INTERVAL_SECONDS == 600


# ======================================================================
# Integration-style: DockerSandbox resource profile enforcement
# ======================================================================


class TestDockerSandboxSecurityConfig:
    """Verify that the security configuration constants match ADR-004."""

    def test_api_security_opts_contain_no_new_privileges(self) -> None:
        assert "no-new-privileges" in DockerSandbox.SECURITY_OPTS

    def test_api_profile_resource_limits(self) -> None:
        p = RESOURCE_PROFILES["api_test"]
        assert p.cpus == 1
        assert p.mem_limit == "512m"
        assert p.timeout == 60
        assert p.read_only is True

    def test_web_profile_resource_limits(self) -> None:
        p = RESOURCE_PROFILES["web_test"]
        assert p.cpus == 2
        assert p.mem_limit == "2g"
        assert p.timeout == 120
        assert p.read_only is True

    def test_app_profile_resource_limits(self) -> None:
        p = RESOURCE_PROFILES["app_test"]
        assert p.cpus == 4
        assert p.mem_limit == "4g"
        assert p.timeout == 180
        assert p.read_only is True

    @pytest.mark.asyncio
    async def test_create_passes_correct_docker_args_for_api(self) -> None:
        sb = DockerSandbox()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"container-id\n", b""))

        captured_args: list[str] = []

        async def _capture_exec(*args: str, **kwargs: object) -> MagicMock:
            captured_args.extend(args)
            return mock_proc

        with (
            patch("asyncio.create_subprocess_exec", _capture_exec),
            patch("tempfile.mkdtemp", return_value="/tmp/testagent-xxx"),
        ):
            await sb.create({"image": "test/runner:latest", "task_type": "api_test"})

        args_str = " ".join(captured_args)
        assert "--security-opt" in args_str
        assert "no-new-privileges" in args_str
        assert "--read-only" in args_str
        assert "--cpus" in args_str
        assert "1" in args_str
        assert "--memory" in args_str
        assert "512m" in args_str

    @pytest.mark.asyncio
    async def test_create_passes_correct_docker_args_for_web(self) -> None:
        sb = DockerSandbox()
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"container-id\n", b""))

        captured_args: list[str] = []

        async def _capture_exec(*args: str, **kwargs: object) -> MagicMock:
            captured_args.extend(args)
            return mock_proc

        with (
            patch("asyncio.create_subprocess_exec", _capture_exec),
            patch("tempfile.mkdtemp", return_value="/tmp/testagent-xxx"),
        ):
            await sb.create({"image": "test/web-runner:latest", "task_type": "web_test"})

        args_str = " ".join(captured_args)
        assert "--cpus" in args_str
        assert "2" in args_str
        assert "--memory" in args_str
        assert "2g" in args_str
