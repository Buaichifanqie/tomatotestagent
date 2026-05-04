from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from typing import ClassVar

from testagent.common.errors import SandboxError, SandboxTimeoutError
from testagent.common.logging import get_logger
from testagent.harness.sandbox import RESOURCE_PROFILES, SANDBOX_TASK_TYPES

logger = get_logger(__name__)


class DockerSandboxError(SandboxError):
    pass


class DockerSandbox:
    """Docker Container-level isolation (ADR-004).

    Each sandbox is a ephemeral Docker container created with strict
    security defaults and resource limits.  Containers are *always*
    created with ``--rm`` so they are automatically removed when
    stopped.
    """

    SECURITY_OPTS: ClassVar[list[str]] = ["no-new-privileges"]
    NETWORK_OPTS: ClassVar[list[str]] = ["--network", "none"]

    _containers: dict[str, dict[str, object]]

    def __init__(self) -> None:
        self._containers = {}

    async def create(self, config: dict[str, object]) -> str:
        """Provision a Docker container.

        Required config keys
        --------------------
        - ``image``: Docker image name (e.g. ``testagent/api-runner:latest``).
        - ``task_type``: One of ``api_test``, ``web_test``, ``app_test``.

        Optional config keys
        --------------------
        - ``env``: dict of environment variables to pass into the container.
        - ``network``: Docker network name (default: ``none`` -- fully isolated).
        - ``working_dir``: Working directory inside the container.
        - ``volumes``: List of ``host:container`` bind-mount strings.

        Returns
        -------
        A unique ``sandbox_id`` string.
        """
        image = config.get("image")
        if not image or not isinstance(image, str):
            raise DockerSandboxError(
                "Missing or invalid 'image' in sandbox config",
                code="MISSING_IMAGE",
                details={"config": config},
            )

        task_type = config.get("task_type", "api_test")
        if not isinstance(task_type, str) or task_type not in SANDBOX_TASK_TYPES:
            raise DockerSandboxError(
                f"Unknown or missing task_type: {task_type}",
                code="INVALID_TASK_TYPE",
                details={"task_type": task_type, "valid": sorted(SANDBOX_TASK_TYPES)},
            )

        profile = RESOURCE_PROFILES[task_type]
        sandbox_id = f"sandbox-{uuid.uuid4().hex[:12]}"

        cmd = ["docker", "run", "--rm", "-d", "--name", sandbox_id]

        # Security options (ADR-004 hard constraint)
        for opt in self.SECURITY_OPTS:
            cmd.extend(["--security-opt", opt])

        # Read-only root filesystem
        if profile.read_only:
            cmd.append("--read-only")

        # Resource limits
        cmd.extend(["--cpus", str(profile.cpus)])
        cmd.extend(["--memory", profile.mem_limit])

        # Network isolation -- default to no network (whitelist only)
        network = config.get("network")
        if network:
            cmd.extend(["--network", str(network)])
        else:
            cmd.extend(self.NETWORK_OPTS)

        # Environment variables
        env = config.get("env")
        if isinstance(env, dict):
            for k, v in env.items():
                cmd.extend(["-e", f"{k}={v}"])

        # Working directory
        working_dir = config.get("working_dir")
        if working_dir:
            cmd.extend(["--workdir", str(working_dir)])

        # Volume mounts
        volumes = config.get("volumes")
        if isinstance(volumes, list):
            for vol in volumes:
                cmd.extend(["-v", str(vol)])

        tmpdir = tempfile.mkdtemp(prefix=f"testagent-{sandbox_id}-")
        cmd.extend(["-v", f"{tmpdir}:/tmp/testagent:rw"])

        cmd.append(image)

        logger.info(
            "Creating Docker sandbox",
            extra={"sandbox_id": sandbox_id, "image": image, "task_type": task_type},
        )

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)

        if proc.returncode != 0:
            err_msg = stderr.decode().strip() or "unknown error"
            raise DockerSandboxError(
                f"Failed to create Docker container: {err_msg}",
                code="DOCKER_CREATE_FAILED",
                details={"sandbox_id": sandbox_id, "image": image, "cmd": " ".join(cmd)},
            )

        container_id = stdout.decode().strip()
        self._containers[sandbox_id] = {
            "container_id": container_id,
            "image": image,
            "task_type": task_type,
            "tmpdir": tmpdir,
            "created": True,
        }

        logger.info(
            "Docker sandbox created",
            extra={"sandbox_id": sandbox_id, "container_id": container_id},
        )
        return sandbox_id

    async def execute(self, sandbox_id: str, command: str, timeout: int | None = None) -> dict[str, object]:
        """Execute a command inside the container.

        The container is killed (``docker kill``) if execution exceeds
        the configured timeout -- this is a **hard** deadline.
        """
        meta = self._containers.get(sandbox_id)
        if meta is None:
            raise DockerSandboxError(
                f"Unknown sandbox: {sandbox_id}",
                code="SANDBOX_NOT_FOUND",
                details={"sandbox_id": sandbox_id},
            )

        task_type = str(meta["task_type"])
        profile = RESOURCE_PROFILES.get(task_type)
        effective_timeout = timeout if timeout is not None else (profile.timeout if profile else 60)

        container_id = str(meta["container_id"])

        exec_cmd = [
            "docker",
            "exec",
            container_id,
            "sh",
            "-c",
            command,
        ]

        logger.debug(
            "Executing in sandbox",
            extra={"sandbox_id": sandbox_id, "command": command, "timeout": effective_timeout},
        )

        proc = await asyncio.create_subprocess_exec(
            *exec_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=effective_timeout,
            )
        except TimeoutError:
            logger.warning(
                "Sandbox execution timed out -- killing container",
                extra={"sandbox_id": sandbox_id, "timeout": effective_timeout},
            )
            await self._force_kill(container_id)
            raise SandboxTimeoutError(
                f"Execution timed out after {effective_timeout}s",
                code="EXECUTION_TIMEOUT",
                details={"sandbox_id": sandbox_id, "timeout": effective_timeout},
            ) from None

        return {
            "exit_code": proc.returncode or 0,
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
        }

    async def get_logs(self, sandbox_id: str) -> str:
        meta = self._containers.get(sandbox_id)
        if meta is None:
            raise DockerSandboxError(
                f"Unknown sandbox: {sandbox_id}",
                code="SANDBOX_NOT_FOUND",
                details={"sandbox_id": sandbox_id},
            )

        container_id = str(meta["container_id"])
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "logs",
            container_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
        return output.strip()

    async def get_artifacts(self, sandbox_id: str) -> list[dict[str, object]]:
        meta = self._containers.get(sandbox_id)
        if meta is None:
            raise DockerSandboxError(
                f"Unknown sandbox: {sandbox_id}",
                code="SANDBOX_NOT_FOUND",
                details={"sandbox_id": sandbox_id},
            )

        tmpdir = meta.get("tmpdir")
        if not tmpdir or not os.path.isdir(str(tmpdir)):
            return []

        artifacts: list[dict[str, object]] = []
        for fname in os.listdir(str(tmpdir)):
            fpath = os.path.join(str(tmpdir), fname)
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

    async def destroy(self, sandbox_id: str) -> None:
        """Destroy the sandbox -- idempotent, safe to call multiple times."""
        meta = self._containers.pop(sandbox_id, None)
        if meta is None:
            logger.debug("Sandbox already destroyed or unknown", extra={"sandbox_id": sandbox_id})
            return

        container_id = str(meta["container_id"])

        # Force-kill and remove
        try:
            await self._force_kill(container_id)
        except Exception:
            logger.warning("Failed to kill container (may already be stopped)", extra={"sandbox_id": sandbox_id})

        try:
            remove_proc = await asyncio.create_subprocess_exec(
                "docker",
                "rm",
                "--force",
                "--volumes",
                container_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await remove_proc.communicate()
        except Exception:
            logger.warning("Failed to remove container (may already be removed)", extra={"sandbox_id": sandbox_id})

        # Clean up temporary data -- "用后即焚"
        tmpdir = meta.get("tmpdir")
        if tmpdir and os.path.isdir(str(tmpdir)):
            shutil.rmtree(str(tmpdir), ignore_errors=True)
            logger.debug("Cleaned up sandbox temp files", extra={"sandbox_id": sandbox_id, "tmpdir": tmpdir})

        logger.info("Docker sandbox destroyed", extra={"sandbox_id": sandbox_id})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _force_kill(container_id: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "kill",
            container_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()

    @staticmethod
    def _guess_mime(fname: str) -> str:
        ext = os.path.splitext(fname)[1].lower()
        mapping = {
            ".json": "application/json",
            ".html": "text/html",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".txt": "text/plain",
            ".xml": "application/xml",
            ".csv": "text/csv",
            ".zip": "application/zip",
        }
        return mapping.get(ext, "application/octet-stream")
