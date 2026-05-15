from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from typing import ClassVar

from testagent.common.errors import SandboxError, SandboxTimeoutError
from testagent.common.logging import get_logger

logger = get_logger(__name__)


class LocalProcessSandboxError(SandboxError):
    pass


class LocalProcessSandbox:
    """Local process execution sandbox.

    **WARNING**: This sandbox provides **no** isolation — it runs
    commands directly on the host.  It is intended for **local
    development / debugging only** and MUST NOT be used in production
    or CI environments (see ADR-004 and AGENTS.md Don't #10).

    To guard against accidental production use, :meth:`create` raises
    :class:`LocalProcessSandboxError` if the ``TESTAGENT_ALLOW_LOCAL``
    environment variable is not set to ``"1"``.
    """

    _ENV_GUARD: ClassVar[str] = "TESTAGENT_ALLOW_LOCAL"

    _sandboxes: dict[str, dict[str, object]]

    def __init__(self) -> None:
        self._sandboxes = {}

    async def create(self, config: dict[str, object]) -> str:
        """Prepare a local working directory for execution.

        Guards against production use via environment variable check.

        Args:
            config: May contain ``working_dir`` to override the
                    auto-created temporary directory.

        Returns:
            A unique sandbox_id.
        """
        if os.environ.get(self._ENV_GUARD) != "1":
            raise LocalProcessSandboxError(
                f"LocalProcessSandbox is only allowed in development. Set {self._ENV_GUARD}=1 to enable.",
                code="LOCAL_SANDBOX_FORBIDDEN",
                details={"env_guard": self._ENV_GUARD},
            )

        sandbox_id = f"local-{uuid.uuid4().hex[:12]}"
        working_dir = config.get("working_dir")
        if working_dir and isinstance(working_dir, str):
            work_dir = os.path.abspath(working_dir)
            os.makedirs(work_dir, exist_ok=True)
        else:
            work_dir = tempfile.mkdtemp(prefix=f"testagent-local-{sandbox_id}-")

        self._sandboxes[sandbox_id] = {
            "working_dir": work_dir,
            "created": True,
        }

        logger.info(
            "Local sandbox created",
            extra={"sandbox_id": sandbox_id, "working_dir": work_dir},
        )
        return sandbox_id

    async def execute(self, sandbox_id: str, command: str, timeout: int | None = None) -> dict[str, object]:
        meta = self._sandboxes.get(sandbox_id)
        if meta is None:
            raise LocalProcessSandboxError(
                f"Unknown sandbox: {sandbox_id}",
                code="SANDBOX_NOT_FOUND",
                details={"sandbox_id": sandbox_id},
            )

        effective_timeout = timeout if timeout is not None else 60
        working_dir = str(meta["working_dir"])

        logger.debug(
            "Executing in local sandbox",
            extra={"sandbox_id": sandbox_id, "cwd": working_dir, "command": command},
        )

        proc = await asyncio.create_subprocess_exec(
            "sh",
            "-c",
            command,
            cwd=working_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=effective_timeout,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise SandboxTimeoutError(
                f"Local execution timed out after {effective_timeout}s",
                code="EXECUTION_TIMEOUT",
                details={"sandbox_id": sandbox_id, "timeout": effective_timeout},
            ) from None

        return {
            "exit_code": proc.returncode or 0,
            "stdout": stdout.decode(errors="replace"),
            "stderr": stderr.decode(errors="replace"),
        }

    async def get_logs(self, sandbox_id: str) -> str:
        meta = self._sandboxes.get(sandbox_id)
        if meta is None:
            raise LocalProcessSandboxError(
                f"Unknown sandbox: {sandbox_id}",
                code="SANDBOX_NOT_FOUND",
                details={"sandbox_id": sandbox_id},
            )
        return "LocalProcessSandbox does not capture persistent logs. Use execute() output."

    async def get_artifacts(self, sandbox_id: str) -> list[dict[str, object]]:
        meta = self._sandboxes.get(sandbox_id)
        if meta is None:
            raise LocalProcessSandboxError(
                f"Unknown sandbox: {sandbox_id}",
                code="SANDBOX_NOT_FOUND",
                details={"sandbox_id": sandbox_id},
            )

        working_dir = str(meta["working_dir"])
        if not os.path.isdir(working_dir):
            return []

        artifacts: list[dict[str, object]] = []
        for fname in os.listdir(working_dir):
            fpath = os.path.join(working_dir, fname)
            if os.path.isfile(fpath):
                stat = os.stat(fpath)
                artifacts.append(
                    {
                        "name": fname,
                        "path": fpath,
                        "size_bytes": stat.st_size,
                    }
                )
        return artifacts

    async def get_tmpdir(self, sandbox_id: str) -> str:
        meta = self._sandboxes.get(sandbox_id)
        if meta is None:
            raise LocalProcessSandboxError(
                f"Unknown sandbox: {sandbox_id}",
                code="SANDBOX_NOT_FOUND",
                details={"sandbox_id": sandbox_id},
            )
        return str(meta["working_dir"])

    async def destroy(self, sandbox_id: str) -> None:
        meta = self._sandboxes.pop(sandbox_id, None)
        if meta is None:
            return

        working_dir = meta.get("working_dir")
        if working_dir and os.path.isdir(str(working_dir)):
            shutil.rmtree(str(working_dir), ignore_errors=True)
            logger.debug(
                "Cleaned up local sandbox working directory",
                extra={"sandbox_id": sandbox_id, "working_dir": working_dir},
            )

        logger.info("Local sandbox destroyed", extra={"sandbox_id": sandbox_id})
