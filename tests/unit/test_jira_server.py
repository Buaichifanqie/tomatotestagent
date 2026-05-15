from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.mcp_servers.jira_server.server import JiraMCPServer
from testagent.mcp_servers.jira_server.tools import (
    jira_create_issue,
    jira_search_issues,
    jira_update_issue,
)


@pytest.fixture()
def server() -> JiraMCPServer:
    return JiraMCPServer()


class TestJiraCreateIssue:
    async def test_create_issue_returns_issue_key(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "id": "10001",
            "key": "PROJ-42",
            "self": "https://jira.example.com/rest/api/2/issue/10001",
        }

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.jira_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await jira_create_issue(
                base_url="https://jira.example.com",
                auth_token="token123",
                project_key="PROJ",
                summary="Test issue",
            )

        assert result["key"] == "PROJ-42"
        assert result["id"] == "10001"
        assert result["project_key"] == "PROJ"
        assert result["summary"] == "Test issue"
        assert result["issuetype"] == "Task"

    async def test_create_issue_sends_correct_payload(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "10002", "key": "PROJ-43", "self": ""}

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.jira_server.tools.httpx.AsyncClient", return_value=mock_client):
            await jira_create_issue(
                base_url="https://jira.example.com",
                auth_token="token123",
                project_key="PROJ",
                summary="Bug fix needed",
                issuetype="Bug",
                description="Fix the login bug",
                priority="High",
                labels=["backend", "urgent"],
            )

        call_args = mock_client.post.call_args
        assert call_args.args[0] == "https://jira.example.com/rest/api/2/issue"
        payload = call_args.kwargs["json"]
        fields = payload["fields"]
        assert fields["project"]["key"] == "PROJ"
        assert fields["summary"] == "Bug fix needed"
        assert fields["issuetype"]["name"] == "Bug"
        assert fields["priority"]["name"] == "High"
        assert fields["labels"] == ["backend", "urgent"]

    async def test_create_issue_api_error_returns_error(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "errorMessages": ["Project 'INVALID' not found"],
        }
        mock_response.text = "Bad Request"

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.jira_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await jira_create_issue(
                base_url="https://jira.example.com",
                auth_token="token123",
                project_key="INVALID",
                summary="Test",
            )

        assert "error" in result
        assert "400" in result["error"]

    async def test_create_issue_with_custom_fields(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "10003", "key": "PROJ-44", "self": ""}

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.jira_server.tools.httpx.AsyncClient", return_value=mock_client):
            await jira_create_issue(
                base_url="https://jira.example.com",
                auth_token="token123",
                project_key="PROJ",
                summary="Custom fields test",
                custom_fields={"customfield_10010": "Some value"},
            )

        call_args = mock_client.post.call_args
        payload = call_args.kwargs["json"]
        assert payload["fields"]["customfield_10010"] == "Some value"


class TestJiraSearchIssues:
    async def test_search_issues_returns_results(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "total": 2,
            "startAt": 0,
            "maxResults": 50,
            "issues": [
                {"id": "10001", "key": "PROJ-1", "self": "", "fields": {"summary": "First issue"}},
                {"id": "10002", "key": "PROJ-2", "self": "", "fields": {"summary": "Second issue"}},
            ],
        }

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.jira_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await jira_search_issues(
                base_url="https://jira.example.com",
                auth_token="token123",
                jql="project=PROJ",
            )

        assert result["total"] == 2
        assert len(result["issues"]) == 2
        assert result["issues"][0]["key"] == "PROJ-1"
        assert result["issues"][1]["key"] == "PROJ-2"

    async def test_search_issues_sends_correct_jql(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"total": 0, "startAt": 0, "maxResults": 10, "issues": []}

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.jira_server.tools.httpx.AsyncClient", return_value=mock_client):
            await jira_search_issues(
                base_url="https://jira.example.com",
                auth_token="token123",
                jql="assignee=currentUser() AND status='In Progress'",
                max_results=10,
                fields=["summary", "status"],
            )

        call_args = mock_client.get.call_args
        assert call_args.args[0] == "https://jira.example.com/rest/api/2/search"
        params = call_args.kwargs["params"]
        assert params["jql"] == "assignee=currentUser() AND status='In Progress'"
        assert params["maxResults"] == 10
        assert params["fields"] == ["summary", "status"]

    async def test_search_issues_pagination(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "total": 1,
            "startAt": 25,
            "maxResults": 25,
            "issues": [{"id": "10003", "key": "PROJ-3", "self": "", "fields": {}}],
        }

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.jira_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await jira_search_issues(
                base_url="https://jira.example.com",
                auth_token="token123",
                jql="project=PROJ",
                start_at=25,
                max_results=25,
            )

        assert result["start_at"] == 25
        assert result["max_results"] == 25

    async def test_search_issues_api_error_returns_error(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.json.return_value = {}
        mock_response.text = "Unauthorized"

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.jira_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await jira_search_issues(
                base_url="https://jira.example.com",
                auth_token="bad_token",
                jql="project=PROJ",
            )

        assert "error" in result


class TestJiraUpdateIssue:
    async def test_update_issue_summary(self) -> None:
        mock_put_response = MagicMock()
        mock_put_response.status_code = 204

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.put = AsyncMock(return_value=mock_put_response)

        with patch("testagent.mcp_servers.jira_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await jira_update_issue(
                base_url="https://jira.example.com",
                auth_token="token123",
                issue_key="PROJ-42",
                summary="Updated summary",
            )

        assert result["success"] is True
        assert result["issue_key"] == "PROJ-42"

    async def test_update_issue_sends_correct_payload(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 204

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.put = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.jira_server.tools.httpx.AsyncClient", return_value=mock_client):
            await jira_update_issue(
                base_url="https://jira.example.com",
                auth_token="token123",
                issue_key="PROJ-42",
                priority="Low",
                labels=["frontend"],
            )

        call_args = mock_client.put.call_args
        assert call_args.args[0] == "https://jira.example.com/rest/api/2/issue/PROJ-42"
        payload = call_args.kwargs["json"]
        assert payload["fields"]["priority"]["name"] == "Low"
        assert payload["fields"]["labels"] == ["frontend"]

    async def test_update_issue_api_error_returns_error(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.json.return_value = {
            "errorMessages": ["Issue does not exist"],
        }

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.put = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.jira_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await jira_update_issue(
                base_url="https://jira.example.com",
                auth_token="token123",
                issue_key="INVALID-999",
            )

        assert "error" in result


class TestJiraMCPServer:
    def test_server_name_is_jira_server(self, server: JiraMCPServer) -> None:
        assert server.server_name == "jira_server"

    async def test_list_tools_returns_three_tools(self, server: JiraMCPServer) -> None:
        tools = await server.list_tools()
        assert len(tools) == 3
        tool_names = {t["name"] for t in tools}
        assert tool_names == {"jira_create_issue", "jira_search_issues", "jira_update_issue"}

    async def test_list_tools_input_schemas_have_required_fields(self, server: JiraMCPServer) -> None:
        tools = await server.list_tools()
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert "required" in tool["inputSchema"]

    async def test_call_tool_jira_create_issue_dispatches(self, server: JiraMCPServer) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"id": "10001", "key": "PROJ-1", "self": ""}

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.jira_server.tools.httpx.AsyncClient", return_value=mock_client):
            raw_result = await server.call_tool(
                "jira_create_issue",
                {
                    "base_url": "https://jira.example.com",
                    "auth_token": "token123",
                    "project_key": "PROJ",
                    "summary": "Test issue",
                },
            )

        result = json.loads(str(raw_result))
        assert result["key"] == "PROJ-1"

    async def test_call_tool_jira_search_issues_dispatches(self, server: JiraMCPServer) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"total": 0, "startAt": 0, "maxResults": 50, "issues": []}

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.jira_server.tools.httpx.AsyncClient", return_value=mock_client):
            raw_result = await server.call_tool(
                "jira_search_issues",
                {
                    "base_url": "https://jira.example.com",
                    "auth_token": "token123",
                    "jql": "project=PROJ",
                },
            )

        result = json.loads(str(raw_result))
        assert result["total"] == 0

    async def test_call_tool_unknown_tool_returns_error(self, server: JiraMCPServer) -> None:
        raw_result = await server.call_tool("unknown_tool", {})
        result = json.loads(str(raw_result))
        assert "error" in result
        assert "Unknown tool" in result["error"]

    async def test_list_resources_returns_project_config(self, server: JiraMCPServer) -> None:
        resources = await server.list_resources()
        assert len(resources) == 1
        assert resources[0]["uri"] == "project://config"
        assert resources[0]["name"] == "Project Configuration"

    async def test_list_resources_have_required_fields(self, server: JiraMCPServer) -> None:
        resources = await server.list_resources()
        for resource in resources:
            assert "uri" in resource
            assert "name" in resource
            assert "mimeType" in resource
            assert "description" in resource

    async def test_health_check_returns_true_when_healthy(self, server: JiraMCPServer) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.jira_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await server.health_check()

        assert result is True

    async def test_health_check_returns_false_when_unhealthy(self, server: JiraMCPServer) -> None:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(side_effect=Exception("connection failed"))

        with patch("testagent.mcp_servers.jira_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await server.health_check()

        assert result is False
