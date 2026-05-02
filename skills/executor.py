from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from testagent.common.logging import get_logger

if TYPE_CHECKING:
    from testagent.gateway.mcp_registry import MCPRegistry
    from testagent.models.skill import SkillDefinition

logger = get_logger(__name__)


@dataclass
class SkillStepResult:
    step_index: int
    step_name: str
    status: str = "pending"
    output: dict[str, Any] | None = None
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class SkillResult:
    skill_name: str
    skill_version: str
    status: str = "pending"
    step_results: list[SkillStepResult] = field(default_factory=list)
    error: str | None = None
    duration_ms: float = 0.0


class SkillExecutor:
    def __init__(self, mcp_registry: MCPRegistry | None = None) -> None:
        self._mcp_registry = mcp_registry

    async def execute(
        self,
        skill: SkillDefinition,
        context: dict[str, Any] | None = None,
    ) -> SkillResult:
        _context = context or {}
        start_time = time.monotonic()

        logger.info(
            "Skill execution started",
            extra={"extra_data": {"skill": skill.name, "version": skill.version}},
        )

        validation = await self._validate_prerequisites(skill)
        if not validation["valid"]:
            duration = (time.monotonic() - start_time) * 1000
            logger.error(
                "Skill prerequisite validation failed",
                extra={"extra_data": {"skill": skill.name, "error": validation["error"]}},
            )
            return SkillResult(
                skill_name=skill.name,
                skill_version=skill.version,
                status="error",
                error=validation["error"],
                duration_ms=duration,
            )

        steps = self._parse_steps(skill)
        step_results: list[SkillStepResult] = []

        for step in steps:
            step_result = await self._execute_step(skill, step, _context)
            step_results.append(step_result)

        total_duration = (time.monotonic() - start_time) * 1000

        all_passed = all(r.status == "passed" for r in step_results)
        any_error = any(r.status == "error" for r in step_results)
        if any_error:
            overall_status = "error"
        elif all_passed:
            overall_status = "passed"
        else:
            overall_status = "failed"

        result = SkillResult(
            skill_name=skill.name,
            skill_version=skill.version,
            status=overall_status,
            step_results=step_results,
            duration_ms=total_duration,
        )

        logger.info(
            "Skill execution completed",
            extra={
                "extra_data": {
                    "skill": skill.name,
                    "status": overall_status,
                    "duration_ms": total_duration,
                    "steps": len(step_results),
                }
            },
        )

        return result

    async def _validate_prerequisites(self, skill: SkillDefinition) -> dict[str, Any]:
        required_servers = skill.required_mcp_servers
        if not required_servers or self._mcp_registry is None:
            return {"valid": True}

        missing: list[str] = []
        if isinstance(required_servers, list):
            for server_name in required_servers:
                if isinstance(server_name, str) and not self._mcp_registry.is_registered(server_name):
                    missing.append(server_name)
        elif isinstance(required_servers, dict):
            server_list = required_servers.get("servers", [])
            if isinstance(server_list, list):
                for s in server_list:
                    if isinstance(s, str) and not self._mcp_registry.is_registered(s):
                        missing.append(s)

        if missing:
            return {
                "valid": False,
                "error": f"Required MCP servers not registered: {', '.join(missing)}",
            }
        return {"valid": True}

    def _parse_steps(self, skill: SkillDefinition) -> list[dict[str, Any]]:
        body = skill.body or ""
        if not body.strip():
            return []

        steps: list[dict[str, Any]] = []
        current_step: dict[str, Any] | None = None
        step_index = 0

        for line in body.split("\n"):
            stripped = line.strip()
            if stripped.startswith("### ") or stripped.startswith("## ") or stripped.startswith("# "):
                if current_step is not None:
                    current_step["content"] = current_step.get("content", "").strip()
                    steps.append(current_step)

                heading = stripped.lstrip("#").strip()
                current_step = {
                    "index": step_index,
                    "name": heading,
                    "content": "",
                }
                step_index += 1
            elif current_step is not None:
                if current_step.get("content"):
                    current_step["content"] += "\n" + stripped
                else:
                    current_step["content"] = stripped

        if current_step is not None:
            current_step["content"] = current_step.get("content", "").strip()
            steps.append(current_step)

        if not steps and body.strip():
            steps.append(
                {
                    "index": 0,
                    "name": "body",
                    "content": body.strip(),
                }
            )

        return steps

    async def _execute_step(
        self,
        skill: SkillDefinition,
        step: dict[str, Any],
        context: dict[str, Any],
    ) -> SkillStepResult:
        step_start = time.monotonic()
        step_name = step.get("name", f"step_{step.get('index', 0)}")

        logger.debug(
            "Executing skill step",
            extra={"extra_data": {"skill": skill.name, "step": step_name}},
        )

        content = step.get("content", "")
        if not content:
            duration = (time.monotonic() - step_start) * 1000
            return SkillStepResult(
                step_index=step.get("index", 0),
                step_name=step_name,
                status="passed",
                output={"message": "Empty step, skipped"},
                duration_ms=duration,
            )

        if self._mcp_registry is not None:
            mcp_result = await self._try_mcp_execution(content, context)
            if mcp_result is not None:
                duration = (time.monotonic() - step_start) * 1000
                return SkillStepResult(
                    step_index=step.get("index", 0),
                    step_name=step_name,
                    status=mcp_result.get("status", "passed"),
                    output=mcp_result.get("output"),
                    error=mcp_result.get("error"),
                    duration_ms=duration,
                )

        duration = (time.monotonic() - step_start) * 1000
        return SkillStepResult(
            step_index=step.get("index", 0),
            step_name=step_name,
            status="passed",
            output={"content_preview": content[:200]},
            duration_ms=duration,
        )

    async def _try_mcp_execution(
        self,
        content: str,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        return None
