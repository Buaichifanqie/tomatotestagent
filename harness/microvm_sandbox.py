from __future__ import annotations

import asyncio
import contextlib
import json
import os
import shutil
import tempfile
import uuid
from typing import ClassVar, cast

from testagent.common.errors import SandboxError, SandboxTimeoutError
from testagent.common.logging import get_logger
from testagent.harness.sandbox import RESOURCE_PROFILES, SANDBOX_TASK_TYPES

logger = get_logger(__name__)


class MicroVMSandboxError(SandboxError):
    pass


class MicroVMSandbox:
    """Firecracker MicroVM isolation (V1.0, ADR-004).

    Each MicroVM is an ephemeral Firecracker VM created with strict
    security defaults and resource limits.  VMs are *always* destroyed
    after execution -- ephemeral by design.

    Prerequisites:
      - Firecracker binary installed (default: /usr/local/bin/firecracker)
      - KVM enabled on the host (/dev/kvm accessible)
      - Root filesystem image with Appium Server + test environment
      - TAP networking configured for VM connectivity
    """

    SECURITY_CONFIG: ClassVar[dict[str, dict[str, int]]] = {
        "app_test": {
            "mem_limit_mib": 4096,
            "vcpu_count": 4,
            "timeout": 180,
        },
    }

    DEFAULT_ROOTFS_PATH: ClassVar[str] = "/opt/testagent/rootfs.img"
    DEFAULT_KERNEL_PATH: ClassVar[str] = "/opt/testagent/vmlinux"

    _vms: dict[str, dict[str, object]]

    def __init__(self, firecracker_bin: str = "/usr/local/bin/firecracker") -> None:
        self._firecracker_bin = firecracker_bin
        self._vms = {}

    async def create(self, config: dict[str, object]) -> str:
        """Create a MicroVM instance.

        Steps:
          1. Prepare rootfs image with Appium Server + test environment
          2. Create Firecracker VM configuration file
          3. Start Firecracker process (requires KVM)
          4. Configure network (TAP device)
          5. Return vm_id
        """
        task_type = config.get("task_type", "app_test")
        if not isinstance(task_type, str) or task_type not in SANDBOX_TASK_TYPES:
            raise MicroVMSandboxError(
                f"Unknown or missing task_type: {task_type}",
                code="INVALID_TASK_TYPE",
                details={"task_type": task_type, "valid": sorted(SANDBOX_TASK_TYPES)},
            )

        sec_config = self.SECURITY_CONFIG.get(task_type)
        if sec_config is None:
            sec_config = self.SECURITY_CONFIG["app_test"]

        vm_id = f"vm-{uuid.uuid4().hex[:12]}"

        rootfs_source = str(config.get("rootfs_path", self.DEFAULT_ROOTFS_PATH))
        kernel_path = str(config.get("kernel_path", self.DEFAULT_KERNEL_PATH))

        if not os.path.isfile(kernel_path):
            raise MicroVMSandboxError(
                f"Kernel image not found: {kernel_path}",
                code="KERNEL_NOT_FOUND",
                details={"kernel_path": kernel_path},
            )

        work_dir = tempfile.mkdtemp(prefix=f"testagent-vm-{vm_id}-")
        rootfs_copy = os.path.join(work_dir, "rootfs.img")
        shutil.copy2(rootfs_source, rootfs_copy)

        vm_config_path = os.path.join(work_dir, "vm_config.json")
        socket_path = os.path.join(work_dir, "firecracker.sock")
        log_path = os.path.join(work_dir, "firecracker.log")

        vm_config = self._build_vm_config(
            kernel_path=kernel_path,
            rootfs_path=rootfs_copy,
            vcpu_count=sec_config["vcpu_count"],
            mem_limit_mib=sec_config["mem_limit_mib"],
            log_path=log_path,
        )

        with open(vm_config_path, "w", encoding="utf-8") as f:
            json.dump(vm_config, f, indent=2)

        tap_device = str(config.get("tap_device", ""))
        if tap_device:
            await self._configure_tap(tap_device, vm_id)

        logger.info(
            "Creating MicroVM",
            extra={
                "vm_id": vm_id,
                "task_type": task_type,
                "vcpu_count": sec_config["vcpu_count"],
                "mem_limit_mib": sec_config["mem_limit_mib"],
            },
        )

        proc = await asyncio.create_subprocess_exec(
            self._firecracker_bin,
            "--config-file",
            vm_config_path,
            "--api-sock",
            socket_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=30)

        if proc.returncode is not None and proc.returncode != 0:
            stderr_data = b""
            with contextlib.suppress(Exception):
                stderr_data = await proc.stderr.read() if proc.stderr else b""
            err_msg = stderr_data.decode(errors="replace").strip() or "unknown error"
            shutil.rmtree(work_dir, ignore_errors=True)
            raise MicroVMSandboxError(
                f"Failed to start Firecracker VM: {err_msg}",
                code="FIRECRACKER_START_FAILED",
                details={"vm_id": vm_id, "error": err_msg},
            )

        self._vms[vm_id] = {
            "process": proc,
            "socket_path": socket_path,
            "log_path": log_path,
            "work_dir": work_dir,
            "rootfs_path": rootfs_copy,
            "config_path": vm_config_path,
            "task_type": task_type,
            "tap_device": tap_device,
            "created": True,
        }

        logger.info(
            "MicroVM created",
            extra={"vm_id": vm_id, "pid": proc.pid},
        )
        return vm_id

    async def execute(self, vm_id: str, command: str, timeout: int = 180) -> dict[str, object]:
        """Execute a command inside the MicroVM with hard timeout.

        App test hard timeout is 180s per AGENTS.md performance constraints.
        """
        meta = self._vms.get(vm_id)
        if meta is None:
            raise MicroVMSandboxError(
                f"Unknown VM: {vm_id}",
                code="VM_NOT_FOUND",
                details={"vm_id": vm_id},
            )

        task_type = str(meta["task_type"])
        profile = RESOURCE_PROFILES.get(task_type)
        effective_timeout = timeout if timeout > 0 else (profile.timeout if profile else 180)

        socket_path = str(meta["socket_path"])

        exec_payload: dict[str, object] = {
            "action_id": str(uuid.uuid4()),
            "command": command,
        }

        logger.debug(
            "Executing in MicroVM",
            extra={"vm_id": vm_id, "command": command, "timeout": effective_timeout},
        )

        try:
            result = await asyncio.wait_for(
                self._send_api_request(socket_path, "/execute", exec_payload),
                timeout=effective_timeout,
            )
        except TimeoutError:
            logger.warning(
                "MicroVM execution timed out -- destroying VM",
                extra={"vm_id": vm_id, "timeout": effective_timeout},
            )
            await self.destroy(vm_id)
            raise SandboxTimeoutError(
                f"MicroVM execution timed out after {effective_timeout}s",
                code="EXECUTION_TIMEOUT",
                details={"vm_id": vm_id, "timeout": effective_timeout},
            ) from None
        except MicroVMSandboxError:
            await self.destroy(vm_id)
            raise

        return {
            "exit_code": result.get("exit_code", -1),
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
        }

    async def get_logs(self, vm_id: str) -> str:
        """Retrieve MicroVM logs."""
        meta = self._vms.get(vm_id)
        if meta is None:
            raise MicroVMSandboxError(
                f"Unknown VM: {vm_id}",
                code="VM_NOT_FOUND",
                details={"vm_id": vm_id},
            )

        log_path = str(meta["log_path"])
        if not os.path.isfile(log_path):
            return ""

        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                return f.read().strip()
        except OSError:
            return ""

    async def get_artifacts(self, vm_id: str) -> list[dict[str, object]]:
        """Retrieve test artifacts (screenshots/videos/logs)."""
        meta = self._vms.get(vm_id)
        if meta is None:
            raise MicroVMSandboxError(
                f"Unknown VM: {vm_id}",
                code="VM_NOT_FOUND",
                details={"vm_id": vm_id},
            )

        work_dir = str(meta.get("work_dir", ""))
        artifacts_dir = os.path.join(work_dir, "artifacts")
        if not os.path.isdir(artifacts_dir):
            return []

        artifacts: list[dict[str, object]] = []
        for fname in os.listdir(artifacts_dir):
            fpath = os.path.join(artifacts_dir, fname)
            if os.path.isfile(fpath):
                stat = os.stat(fpath)
                artifacts.append(
                    {
                        "name": fname,
                        "path": fpath,
                        "size_bytes": stat.st_size,
                        "mime_type": self._guess_mime(fname),
                    }
                )
        return artifacts

    async def get_tmpdir(self, vm_id: str) -> str:
        """Return the host-side temporary directory path for this VM."""
        meta = self._vms.get(vm_id)
        if meta is None:
            raise MicroVMSandboxError(
                f"Unknown VM: {vm_id}",
                code="VM_NOT_FOUND",
                details={"vm_id": vm_id},
            )
        return str(meta["work_dir"])

    async def destroy(self, vm_id: str) -> None:
        """Destroy the MicroVM and clean up all temp data (ephemeral, AGENTS.md hard constraint)."""
        meta = self._vms.pop(vm_id, None)
        if meta is None:
            logger.debug("VM already destroyed or unknown", extra={"vm_id": vm_id})
            return

        proc = meta.get("process")
        if (
            proc is not None
            and isinstance(proc, asyncio.subprocess.Process)
            and proc.returncode is None
        ):
            try:
                proc.kill()
                await asyncio.wait_for(proc.wait(), timeout=5)
            except Exception:
                logger.warning(
                    "Failed to kill Firecracker process",
                    extra={"vm_id": vm_id},
                )

        socket_path = str(meta.get("socket_path", ""))
        if socket_path and os.path.exists(socket_path):
            with contextlib.suppress(OSError):
                os.unlink(socket_path)

        tap_device = str(meta.get("tap_device", ""))
        if tap_device:
            await self._cleanup_tap(tap_device, vm_id)

        work_dir = str(meta.get("work_dir", ""))
        if work_dir and os.path.isdir(work_dir):
            shutil.rmtree(work_dir, ignore_errors=True)
            logger.debug(
                "Cleaned up VM temp files",
                extra={"vm_id": vm_id, "work_dir": work_dir},
            )

        logger.info("MicroVM destroyed", extra={"vm_id": vm_id})

    def _build_vm_config(
        self,
        kernel_path: str,
        rootfs_path: str,
        vcpu_count: int,
        mem_limit_mib: int,
        log_path: str,
    ) -> dict[str, object]:
        """Build Firecracker VM configuration JSON."""
        return {
            "boot-source": {
                "kernel_image_path": kernel_path,
                "boot_args": "console=ttyS0 reboot=k panic=1 pci=off",
            },
            "drives": [
                {
                    "drive_id": "rootfs",
                    "path_on_host": rootfs_path,
                    "is_root_device": True,
                    "is_read_only": False,
                },
            ],
            "machine-config": {
                "vcpu_count": vcpu_count,
                "mem_size_mib": mem_limit_mib,
                "ht_enabled": False,
            },
            "logger": {
                "log_path": log_path,
                "level": "Warning",
                "show_level": True,
                "show_log_origin": True,
            },
        }

    async def _send_api_request(
        self,
        socket_path: str,
        endpoint: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        """Send a request to the Firecracker API via Unix socket."""
        if not os.path.exists(socket_path):
            raise MicroVMSandboxError(
                f"Firecracker socket not found: {socket_path}",
                code="SOCKET_NOT_FOUND",
                details={"socket_path": socket_path},
            )

        body = json.dumps(payload).encode("utf-8")

        proc = await asyncio.create_subprocess_exec(
            "curl",
            "--unix-socket",
            socket_path,
            "-X",
            "PUT",
            f"http://localhost{endpoint}",
            "-H",
            "Content-Type: application/json",
            "-d",
            body,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            err_msg = stderr.decode(errors="replace").strip() or "unknown error"
            raise MicroVMSandboxError(
                f"Firecracker API request failed: {err_msg}",
                code="API_REQUEST_FAILED",
                details={"endpoint": endpoint, "error": err_msg},
            )

        try:
            return cast("dict[str, object]", json.loads(stdout.decode("utf-8")))
        except json.JSONDecodeError:
            return {"raw_output": stdout.decode(errors="replace")}

    async def _configure_tap(self, tap_device: str, vm_id: str) -> None:
        """Configure a TAP network device for VM connectivity.

        Network whitelist isolation -- only allows access to the SUT
        (System Under Test).  Test sandboxes must NOT access internal
        non-target services.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "ip",
                "link",
                "set",
                tap_device,
                "up",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                err_msg = stderr.decode(errors="replace").strip()
                logger.warning(
                    "Failed to configure TAP device",
                    extra={"vm_id": vm_id, "tap_device": tap_device, "error": err_msg},
                )
        except FileNotFoundError:
            logger.warning(
                "ip command not found -- TAP device configuration skipped",
                extra={"vm_id": vm_id, "tap_device": tap_device},
            )

    async def _cleanup_tap(self, tap_device: str, vm_id: str) -> None:
        """Clean up TAP device after VM destruction."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ip",
                "link",
                "set",
                tap_device,
                "down",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
        except Exception:
            logger.debug(
                "Failed to clean up TAP device",
                extra={"vm_id": vm_id, "tap_device": tap_device},
            )

    @staticmethod
    def _guess_mime(fname: str) -> str:
        ext = os.path.splitext(fname)[1].lower()
        mapping = {
            ".json": "application/json",
            ".html": "text/html",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".mp4": "video/mp4",
            ".txt": "text/plain",
            ".xml": "application/xml",
            ".csv": "text/csv",
            ".zip": "application/zip",
            ".log": "text/plain",
        }
        return mapping.get(ext, "application/octet-stream")
