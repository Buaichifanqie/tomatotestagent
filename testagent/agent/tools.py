from __future__ import annotations

from typing import TYPE_CHECKING, Any

from testagent.common.logging import get_logger

if TYPE_CHECKING:
    from testagent.gateway.mcp_registry import MCPRegistry
    from testagent.skills.registry import SkillRegistry

logger = get_logger(__name__)


def create_skill_tool(registry: SkillRegistry) -> dict[str, Any]:
    """Create the load_skill tool definition for Layer 2 injection.

    The tool definition is added to the LLM's tool list so the model can
    call load_skill(name) to retrieve the full body of a skill at runtime.
    """
    _ = registry
    return {
        "name": "load_skill",
        "description": (
            "Retrieve a skill's complete instructions by name. Use this for step-by-step guidance during execution."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The name of the skill to load (e.g. 'api_smoke_test').",
                }
            },
            "required": ["name"],
        },
    }


async def handle_load_skill(registry: SkillRegistry, args: dict[str, Any]) -> dict[str, Any]:
    """Handle load_skill tool call from the LLM.

    Looks up the skill by name in the SkillRegistry and returns its full body.
    This is Layer 2 injection: the model only receives the body when it
    explicitly requests it.
    """
    name = args.get("name", "")
    if not name or not isinstance(name, str):
        return {"error": "Missing or invalid 'name' parameter", "found": False}

    skill = registry.get_by_name(name)
    if skill is None:
        logger.warning(
            "load_skill called for unknown skill",
            extra={"extra_data": {"skill_name": name}},
        )
        return {
            "found": False,
            "error": f"Skill '{name}' not found. Available skills:\n{registry.get_descriptions()}",
        }

    body = skill.body or ""
    logger.debug(
        "load_skill returning skill body",
        extra={"extra_data": {"skill": skill.name, "version": skill.version, "body_length": len(body)}},
    )

    return {
        "found": True,
        "name": skill.name,
        "version": skill.version,
        "description": skill.description,
        "trigger_pattern": skill.trigger_pattern or "",
        "body": body,
    }


async def register_mcp_tools(registry: MCPRegistry) -> list[dict[str, Any]]:
    """Collect all tools from all registered MCP Servers for the Agent Loop.

    Returns a list of tool definitions compatible with the LLM's tool calling format.
    Each tool dict contains 'name', 'description', and 'input_schema' keys.
    """
    servers = await registry.list_servers()
    all_tools: list[dict[str, Any]] = []

    for server in servers:
        for tool in server.tools:
            all_tools.append(
                {
                    "name": tool.get("name", ""),
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("input_schema", {}),
                }
            )

    logger.debug(
        "MCP tools collected for Agent Loop",
        extra={"extra_data": {"tool_count": len(all_tools), "server_count": len(servers)}},
    )

    return all_tools
