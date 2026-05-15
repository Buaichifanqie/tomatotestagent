from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from testagent.common import get_logger

if TYPE_CHECKING:
    from testagent.gateway.mcp_registry import MCPRegistry

_logger = get_logger(__name__)

REQUIRED_FIELDS = frozenset(
    {
        "name",
        "version",
        "description",
        "trigger",
        "required_mcp_servers",
        "required_rag_collections",
    }
)

LIST_FIELDS = frozenset({"required_mcp_servers", "required_rag_collections"})


class ValidationResult(BaseModel):
    valid: bool
    errors: list[str] = []
    warnings: list[str] = []
    degraded: bool = False


class SkillValidator:
    def __init__(self, mcp_registry: MCPRegistry | None = None) -> None:
        self._mcp_registry = mcp_registry

    def validate(self, meta: dict[str, object]) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []
        degraded = False

        for field in REQUIRED_FIELDS:
            if field not in meta:
                errors.append(f"Missing required field: '{field}'")
                continue

            value: Any = meta[field]
            if value is None:
                errors.append(f"Required field '{field}' is null")
                continue

            if field in LIST_FIELDS:
                if isinstance(value, list) and len(value) == 0:
                    continue
            elif isinstance(value, str) and not value.strip():
                errors.append(f"Required field '{field}' is empty")

        if errors:
            return ValidationResult(valid=False, errors=errors, warnings=warnings, degraded=degraded)

        trigger = meta.get("trigger", "")
        if not isinstance(trigger, str) or not trigger.strip():
            errors.append("Field 'trigger' must be a non-empty string")
        else:
            try:
                re.compile(str(trigger))
            except re.error as exc:
                errors.append(f"Invalid trigger pattern '{trigger}': {exc}")

        required_mcp: Any = meta.get("required_mcp_servers", [])
        if not isinstance(required_mcp, list):
            errors.append("Field 'required_mcp_servers' must be a list")
        elif self._mcp_registry is not None:
            for server_name in required_mcp:
                if not isinstance(server_name, str):
                    errors.append(f"required_mcp_servers contains non-string value: {server_name!r}")
                    continue
                if not self._mcp_registry.is_registered(str(server_name)):
                    warnings.append(f"MCP Server '{server_name}' not registered, Skill marked as degraded")
                    degraded = True

        required_rag: Any = meta.get("required_rag_collections", [])
        if not isinstance(required_rag, list):
            errors.append("Field 'required_rag_collections' must be a list")

        valid = len(errors) == 0
        return ValidationResult(valid=valid, errors=errors, warnings=warnings, degraded=degraded)
