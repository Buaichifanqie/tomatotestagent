from __future__ import annotations

from testagent.common.errors import SandboxError
from testagent.common.logging import get_logger

logger = get_logger(__name__)


class MicroVMNotImplementedError(SandboxError):
    def __init__(self) -> None:
        super().__init__(
            "MicroVM sandbox is not implemented in MVP — scheduled for V1.0",
            code="MICROVM_NOT_IMPLEMENTED",
        )


class MicroVMSandbox:
    """MicroVM-level sandbox (Firecracker).

    **Not implemented in MVP.** This class serves as a placeholder
    that raises :class:`MicroVMNotImplementedError` on every method
    call.  Full implementation is scheduled for V1.0.
    """

    async def create(self, config: dict[str, object]) -> str:
        raise MicroVMNotImplementedError

    async def execute(self, sandbox_id: str, command: str, timeout: int) -> dict[str, object]:
        raise MicroVMNotImplementedError

    async def get_logs(self, sandbox_id: str) -> str:
        raise MicroVMNotImplementedError

    async def get_artifacts(self, sandbox_id: str) -> list[dict[str, object]]:
        raise MicroVMNotImplementedError

    async def get_tmpdir(self, sandbox_id: str) -> str:
        raise MicroVMNotImplementedError

    async def destroy(self, sandbox_id: str) -> None:
        raise MicroVMNotImplementedError
