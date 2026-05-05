from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ISandbox(Protocol):
    """Sandbox isolation interface.

    All sandbox implementations (Docker, MicroVM, Local) must conform
    to this protocol.  Each sandbox manages its own lifecycle:
    create → execute (zero or more) → destroy.
    """

    async def create(self, config: dict[str, object]) -> str:
        """Provision a new sandbox instance.

        Args:
            config: Implementation-specific configuration (image, resource limits, etc.).

        Returns:
            A unique sandbox identifier (sandbox_id).
        """
        ...

    async def execute(self, sandbox_id: str, command: str, timeout: int) -> dict[str, object]:
        """Run a command inside an existing sandbox.

        Args:
            sandbox_id: Target sandbox identifier returned by create().
            command:   Shell command or script to execute.
            timeout:   Hard timeout in seconds.

        Returns:
            Dict with at least keys:
              - exit_code: int
              - stdout:    str
              - stderr:    str
        """
        ...

    async def get_logs(self, sandbox_id: str) -> str:
        """Retrieve combined (stdout + stderr) logs for a sandbox."""
        ...

    async def get_artifacts(self, sandbox_id: str) -> list[dict[str, object]]:
        """Retrieve artifact metadata (file paths, mime types, sizes) produced
        during execution.  Returns an empty list if no artifacts exist."""
        ...

    async def destroy(self, sandbox_id: str) -> None:
        """Tear down the sandbox and release all resources.

        MUST be idempotent — calling destroy() twice on the same id
        should not raise.  All temporary data MUST be cleaned up
        ("用后即焚").
        """
        ...

    async def get_tmpdir(self, sandbox_id: str) -> str:
        """Return the host-side temporary directory path for this sandbox.

        The directory is shared into the sandbox (mounted at a well-known
        location such as ``/tmp/testagent``) so that the host can place
        files (e.g. generated test scripts) that the sandbox can execute.

        Returns:
            Absolute host path to the temporary directory.

        Raises:
            :class:`SandboxError`: If *sandbox_id* is unknown.
        """
        ...


# ---------------------------------------------------------------------------
# Resource profiles — maps test type → (cpu, memory, timeout)
# ---------------------------------------------------------------------------


class ResourceProfile:
    """Immutable resource quota descriptor."""

    def __init__(self, cpus: int, mem_limit: str, timeout: int, *, read_only: bool = True) -> None:
        self.cpus = cpus
        self.mem_limit = mem_limit
        self.timeout = timeout
        self.read_only = read_only

    def to_dict(self) -> dict[str, object]:
        return {
            "cpus": self.cpus,
            "mem_limit": self.mem_limit,
            "timeout": self.timeout,
            "read_only": self.read_only,
        }


RESOURCE_PROFILES: dict[str, ResourceProfile] = {
    "api_test": ResourceProfile(cpus=1, mem_limit="512m", timeout=60),
    "web_test": ResourceProfile(cpus=2, mem_limit="2g", timeout=120),
    "app_test": ResourceProfile(cpus=4, mem_limit="4g", timeout=180),
}

SANDBOX_TASK_TYPES: set[str] = set(RESOURCE_PROFILES.keys())
