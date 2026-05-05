from __future__ import annotations

import json
from typing import Any, ClassVar

import httpx

from testagent.mcp_servers.base import BaseMCPServer
from testagent.mcp_servers.jira_server.tools import (
    jira_create_issue,
    jira_search_issues,
    jira_update_issue,
)


class JiraMCPServer(BaseMCPServer):
    server_name = "jira_server"

    _tools_spec: ClassVar[list[dict[str, object]]] = [
        {
            "name": "jira_create_issue",
            "description": "Create a new Jira issue, return {id, key, self, project_key, summary, issuetype}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "base_url": {"type": "string", "description": "Jira instance base URL (e.g. https://your-domain.atlassian.net)"},
                    "auth_token": {"type": "string", "description": "Jira API token or Bearer token"},
                    "project_key": {"type": "string", "description": "Project key (e.g. PROJ)"},
                    "summary": {"type": "string", "description": "Issue summary/title"},
                    "issuetype": {
                        "type": "string",
                        "description": "Issue type name (e.g. Task, Bug, Story), default Task",
                    },
                    "description": {"type": "string", "description": "Issue description text"},
                    "priority": {"type": "string", "description": "Priority name (e.g. High, Low)"},
                    "assignee": {"type": "string", "description": "Assignee account ID"},
                    "labels": {"type": "array", "items": {"type": "string"}, "description": "List of labels"},
                    "custom_fields": {"type": "object", "description": "Custom field key-value pairs"},
                },
                "required": ["base_url", "auth_token", "project_key", "summary"],
            },
        },
        {
            "name": "jira_search_issues",
            "description": "Search Jira issues using JQL, return {total, start_at, max_results, issues[]}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "base_url": {"type": "string", "description": "Jira instance base URL"},
                    "auth_token": {"type": "string", "description": "Jira API token or Bearer token"},
                    "jql": {"type": "string", "description": "JQL query string"},
                    "max_results": {"type": "integer", "description": "Maximum results to return, default 50"},
                    "fields": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Fields to include in results",
                    },
                    "start_at": {"type": "integer", "description": "Start index for pagination, default 0"},
                },
                "required": ["base_url", "auth_token", "jql"],
            },
        },
        {
            "name": "jira_update_issue",
            "description": "Update an existing Jira issue, return {success, issue_key}",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "base_url": {"type": "string", "description": "Jira instance base URL"},
                    "auth_token": {"type": "string", "description": "Jira API token or Bearer token"},
                    "issue_key": {"type": "string", "description": "Issue key (e.g. PROJ-123)"},
                    "summary": {"type": "string", "description": "New summary/title"},
                    "description": {"type": "string", "description": "New description text"},
                    "status": {"type": "string", "description": "Target status name (e.g. 'In Progress', 'Done')"},
                    "priority": {"type": "string", "description": "Priority name"},
                    "assignee": {"type": "string", "description": "Assignee account ID"},
                    "labels": {"type": "array", "items": {"type": "string"}, "description": "List of labels"},
                    "custom_fields": {"type": "object", "description": "Custom field key-value pairs"},
                },
                "required": ["base_url", "auth_token", "issue_key"],
            },
        },
    ]

    _tool_registry: ClassVar[dict[str, Any]] = {
        "jira_create_issue": jira_create_issue,
        "jira_search_issues": jira_search_issues,
        "jira_update_issue": jira_update_issue,
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
                "uri": "project://config",
                "name": "Project Configuration",
                "mimeType": "application/json",
                "description": "Jira project configuration including project key, URL, and connection settings",
            },
        ]

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5)) as client:
                response = await client.get("https://httpbin.org/get")
                return response.status_code == 200
        except Exception:
            return False
