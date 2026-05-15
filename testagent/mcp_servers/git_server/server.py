from __future__ import annotations

import json
from typing import Any, ClassVar

from testagent.mcp_servers.base import BaseMCPServer
from testagent.mcp_servers.git_server.tools import git_blame, git_diff, git_log


class GitMCPServer(BaseMCPServer):
    server_name = "git_server"

    _tools_spec: ClassVar[list[dict[str, object]]] = [
        {
            "name": "git_diff",
            "description": "Show git diff between commits or working tree, return {output, exit_code}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Absolute path to the git repository"},
                    "commit_a": {"type": "string", "description": "Base commit hash or reference"},
                    "commit_b": {
                        "type": "string",
                        "description": "Target commit hash (used with commit_a as commit_a..commit_b)",
                    },
                    "path": {"type": "string", "description": "Specific file path to show diff for"},
                    "cached": {"type": "boolean", "description": "Show staged changes (--cached), default false"},
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional git diff arguments",
                    },
                },
                "required": ["repo_path"],
            },
        },
        {
            "name": "git_blame",
            "description": "Show blame information for a file, return {output, exit_code}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Absolute path to the git repository"},
                    "file_path": {"type": "string", "description": "File path relative to repository root"},
                    "start_line": {"type": "integer", "description": "Start line number for blame range"},
                    "end_line": {"type": "integer", "description": "End line number for blame range"},
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional git blame arguments",
                    },
                },
                "required": ["repo_path", "file_path"],
            },
        },
        {
            "name": "git_log",
            "description": "Show commit log, return {output, exit_code}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "repo_path": {"type": "string", "description": "Absolute path to the git repository"},
                    "max_count": {"type": "integer", "description": "Maximum number of commits to show, default 10"},
                    "branch": {"type": "string", "description": "Branch name to show log for"},
                    "file_path": {"type": "string", "description": "Show only commits affecting this file"},
                    "since": {
                        "type": "string",
                        "description": "Show commits more recent than a date (e.g. '2024-01-01', '2 weeks ago')",
                    },
                    "until": {"type": "string", "description": "Show commits older than a date"},
                    "author": {"type": "string", "description": "Filter by author pattern"},
                    "format_str": {
                        "type": "string",
                        "description": "Custom format string (e.g. '%%h %%s')",
                    },
                    "args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional git log arguments",
                    },
                },
                "required": ["repo_path"],
            },
        },
    ]

    _tool_registry: ClassVar[dict[str, Any]] = {
        "git_diff": git_diff,
        "git_blame": git_blame,
        "git_log": git_log,
    }

    async def list_tools(self) -> list[dict[str, object]]:
        return self._tools_spec

    async def call_tool(self, tool_name: str, arguments: dict[str, object]) -> object:
        tool = self._tool_registry.get(tool_name)
        if tool is None:
            return json.dumps({"error": f"Unknown tool: {tool_name}"})
        try:
            from inspect import iscoroutinefunction

            if iscoroutinefunction(tool):
                result = await tool(**arguments)
            else:
                result = tool(**arguments)
            if isinstance(result, (dict, list)):
                return json.dumps(result)
            return str(result)
        except Exception as e:
            return json.dumps({"error": str(e)})

    async def list_resources(self) -> list[dict[str, object]]:
        return [
            {
                "uri": "repo://structure",
                "name": "Repository Structure",
                "mimeType": "application/json",
                "description": "Top-level directory structure of the git repository",
            },
            {
                "uri": "repo://branches",
                "name": "Repository Branches",
                "mimeType": "application/json",
                "description": "List of local and remote branches in the repository",
            },
        ]

    async def health_check(self) -> bool:
        try:
            import shutil

            return shutil.which("git") is not None
        except Exception:
            return False
