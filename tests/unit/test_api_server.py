from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.mcp_servers.api_server.server import APIMCPServer
from testagent.mcp_servers.api_server.tools import (
    api_compare_response,
    api_request,
    api_validate_schema,
)


@pytest.fixture()
def server() -> APIMCPServer:
    return APIMCPServer()


class TestApiRequest:
    async def test_get_request_returns_structured_response(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"message": "ok"}
        mock_response.text = '{"message": "ok"}'

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.request = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.api_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await api_request("GET", "https://api.example.com/data")

        assert result["status_code"] == 200
        assert result["headers"]["content-type"] == "application/json"
        assert result["body"] == {"message": "ok"}
        assert "duration_ms" in result
        assert isinstance(result["duration_ms"], (int, float))

    async def test_post_request_sends_body_and_headers(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.headers = {}
        mock_response.json.return_value = {"id": 1}
        mock_response.text = '{"id": 1}'

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.request = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.api_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await api_request(
                "POST",
                "https://api.example.com/items",
                headers={"Authorization": "Bearer token"},
                body={"name": "test"},
                timeout=15,
            )

        assert result["status_code"] == 201
        assert result["body"] == {"id": 1}
        call_args = mock_client.request.call_args
        assert call_args.kwargs["method"] == "POST"
        assert call_args.kwargs["url"] == "https://api.example.com/items"
        assert call_args.kwargs["headers"] == {"Authorization": "Bearer token"}
        assert call_args.kwargs["json"] == {"name": "test"}

    async def test_non_json_response_body_falls_back_to_text(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.headers = {}
        mock_response.json.side_effect = ValueError("not json")
        mock_response.text = "<html>Error</html>"

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.request = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.api_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await api_request("GET", "https://api.example.com/error")

        assert result["status_code"] == 500
        assert result["body"] == "<html>Error</html>"


class TestApiValidateSchema:
    async def test_valid_body_matches_schema(self) -> None:
        schema: dict[str, object] = {
            "type": "object",
            "required": ["name", "age"],
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer", "minimum": 0},
                "email": {"type": "string", "pattern": r"^[^@]+@[^@]+\.[^@]+$"},
            },
        }
        body: dict[str, object] = {"name": "Alice", "age": 30, "email": "alice@example.com"}

        result = await api_validate_schema(body, schema=schema)
        assert result["valid"] is True
        assert result["errors"] == []

    async def test_invalid_type_reports_error(self) -> None:
        schema: dict[str, object] = {
            "type": "object",
            "properties": {
                "age": {"type": "integer"},
            },
        }
        body: dict[str, object] = {"age": "not_a_number"}

        result = await api_validate_schema(body, schema=schema)
        assert result["valid"] is False
        assert any("expected type 'integer'" in e for e in result["errors"])

    async def test_missing_required_field_reports_error(self) -> None:
        schema: dict[str, object] = {
            "type": "object",
            "required": ["name", "email"],
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
            },
        }
        body: dict[str, object] = {"name": "Bob"}

        result = await api_validate_schema(body, schema=schema)
        assert result["valid"] is False
        assert any("missing required field 'email'" in e for e in result["errors"])

    async def test_string_pattern_violation_reports_error(self) -> None:
        schema: dict[str, object] = {
            "type": "object",
            "properties": {
                "email": {"type": "string", "pattern": r"^[^@]+@[^@]+\.[^@]+$"},
            },
        }
        body: dict[str, object] = {"email": "not-an-email"}

        result = await api_validate_schema(body, schema=schema)
        assert result["valid"] is False
        assert any("does not match pattern" in e for e in result["errors"])

    async def test_string_length_constraints(self) -> None:
        schema: dict[str, object] = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "minLength": 2, "maxLength": 10},
            },
        }
        body: dict[str, object] = {"name": "A"}

        result = await api_validate_schema(body, schema=schema)
        assert result["valid"] is False
        assert any("minLength" in e for e in result["errors"])

    async def test_number_range_constraints(self) -> None:
        schema: dict[str, object] = {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "minimum": 0, "maximum": 100},
            },
        }
        body: dict[str, object] = {"score": 150}

        result = await api_validate_schema(body, schema=schema)
        assert result["valid"] is False
        assert any("maximum" in e for e in result["errors"])

    async def test_enum_violation_reports_error(self) -> None:
        schema: dict[str, object] = {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["active", "inactive"]},
            },
        }
        body: dict[str, object] = {"status": "pending"}

        result = await api_validate_schema(body, schema=schema)
        assert result["valid"] is False
        assert any("not in enum" in e for e in result["errors"])

    async def test_nested_object_validation(self) -> None:
        schema: dict[str, object] = {
            "type": "object",
            "properties": {
                "user": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {
                        "id": {"type": "integer"},
                        "name": {"type": "string"},
                    },
                },
            },
        }
        body: dict[str, object] = {"user": {"id": 1, "name": "Alice"}}

        result = await api_validate_schema(body, schema=schema)
        assert result["valid"] is True
        assert result["errors"] == []

    async def test_array_item_validation(self) -> None:
        schema: dict[str, object] = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
            },
        }
        body: dict[str, object] = {"items": [1, 2, "bad"]}

        result = await api_validate_schema(body, schema=schema)
        assert result["valid"] is False
        assert any("expected type" in e for e in result["errors"])

    async def test_fetches_schema_from_url(self) -> None:
        remote_schema: dict[str, object] = {
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "integer"}},
        }
        mock_response = MagicMock()
        mock_response.json.return_value = remote_schema
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(return_value=mock_response)

        body: dict[str, object] = {"id": 1}
        with patch("testagent.mcp_servers.api_server.tools.httpx.AsyncClient", return_value=mock_client):
            result = await api_validate_schema(body, schema_url="https://schemas.example.com/user.json")

        assert result["valid"] is True
        assert result["errors"] == []

    async def test_no_schema_returns_valid(self) -> None:
        body: dict[str, object] = {"any": "thing"}
        result = await api_validate_schema(body)
        assert result["valid"] is True
        assert result["errors"] == []


class TestApiCompareResponse:
    async def test_exact_match_returns_true(self) -> None:
        a: dict[str, object] = {"id": 1, "name": "Alice"}
        b: dict[str, object] = {"id": 1, "name": "Alice"}

        result = await api_compare_response(a, b)
        assert result["match"] is True
        assert result["diff_fields"] == []

    async def test_value_mismatch_returns_false_with_diff(self) -> None:
        a: dict[str, object] = {"id": 1, "name": "Alice"}
        b: dict[str, object] = {"id": 2, "name": "Alice"}

        result = await api_compare_response(a, b)
        assert result["match"] is False
        assert any("$.id" in d for d in result["diff_fields"])

    async def test_missing_field_in_a_reports_diff(self) -> None:
        a: dict[str, object] = {"id": 1}
        b: dict[str, object] = {"id": 1, "name": "Bob"}

        result = await api_compare_response(a, b)
        assert result["match"] is False
        assert any("missing in response_a" in d for d in result["diff_fields"])

    async def test_missing_field_in_b_reports_diff(self) -> None:
        a: dict[str, object] = {"id": 1, "name": "Bob"}
        b: dict[str, object] = {"id": 1}

        result = await api_compare_response(a, b)
        assert result["match"] is False
        assert any("missing in response_b" in d for d in result["diff_fields"])

    async def test_ignore_fields_excludes_specified_paths(self) -> None:
        a: dict[str, object] = {"id": 1, "name": "Alice", "timestamp": "2024-01-01T00:00:00Z"}
        b: dict[str, object] = {"id": 1, "name": "Alice", "timestamp": "2024-06-15T12:30:00Z"}

        result = await api_compare_response(a, b, ignore_fields=["timestamp"])
        assert result["match"] is True
        assert result["diff_fields"] == []

    async def test_ignore_fields_with_dollar_prefix(self) -> None:
        a: dict[str, object] = {"id": 1, "created_at": "old"}
        b: dict[str, object] = {"id": 1, "created_at": "new"}

        result = await api_compare_response(a, b, ignore_fields=["$.created_at"])
        assert result["match"] is True
        assert result["diff_fields"] == []

    async def test_nested_object_diff(self) -> None:
        a: dict[str, object] = {"user": {"id": 1, "name": "Alice"}}
        b: dict[str, object] = {"user": {"id": 1, "name": "Bob"}}

        result = await api_compare_response(a, b)
        assert result["match"] is False
        assert any("$.user.name" in d for d in result["diff_fields"])

    async def test_nested_ignore_fields(self) -> None:
        a: dict[str, object] = {"user": {"id": 1, "name": "Alice", "updated_at": "old"}}
        b: dict[str, object] = {"user": {"id": 1, "name": "Alice", "updated_at": "new"}}

        result = await api_compare_response(a, b, ignore_fields=["$.user.updated_at"])
        assert result["match"] is True
        assert result["diff_fields"] == []

    async def test_array_length_mismatch(self) -> None:
        a: dict[str, object] = {"items": [1, 2]}
        b: dict[str, object] = {"items": [1, 2, 3]}

        result = await api_compare_response(a, b)
        assert result["match"] is False
        assert any("length mismatch" in d for d in result["diff_fields"])

    async def test_array_element_value_mismatch(self) -> None:
        a: dict[str, object] = {"items": [1, 2, 3]}
        b: dict[str, object] = {"items": [1, 99, 3]}

        result = await api_compare_response(a, b)
        assert result["match"] is False
        assert any("$." in d and "value mismatch" in d for d in result["diff_fields"])

    async def test_type_mismatch_reports_diff(self) -> None:
        a: dict[str, object] = {"value": 42}
        b: dict[str, object] = {"value": "42"}

        result = await api_compare_response(a, b)
        assert result["match"] is False
        assert any("type mismatch" in d for d in result["diff_fields"])


class TestAPIMCPServer:
    def test_server_name_is_api_server(self, server: APIMCPServer) -> None:
        assert server.server_name == "api_server"

    async def test_list_tools_returns_three_tools(self, server: APIMCPServer) -> None:
        tools = await server.list_tools()
        assert len(tools) == 3
        tool_names = {t["name"] for t in tools}
        assert tool_names == {"api_request", "api_validate_schema", "api_compare_response"}

    async def test_list_tools_input_schemas_have_required_fields(self, server: APIMCPServer) -> None:
        tools = await server.list_tools()
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert "required" in tool["inputSchema"]

    async def test_call_tool_api_request_dispatches_correctly(self, server: APIMCPServer) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {}
        mock_response.json.return_value = {"data": "test"}
        mock_response.text = '{"data": "test"}'

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.request = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.api_server.server.httpx.AsyncClient", return_value=mock_client):
            raw_result = await server.call_tool("api_request", {"method": "GET", "url": "https://api.example.com"})

        result = json.loads(str(raw_result))
        assert result["status_code"] == 200
        assert result["body"] == {"data": "test"}

    async def test_call_tool_api_compare_response_dispatches_correctly(self, server: APIMCPServer) -> None:
        raw_result = await server.call_tool(
            "api_compare_response",
            {"response_a": {"id": 1}, "response_b": {"id": 1}},
        )
        result = json.loads(str(raw_result))
        assert result["match"] is True

    async def test_call_tool_unknown_tool_returns_error(self, server: APIMCPServer) -> None:
        raw_result = await server.call_tool("unknown_tool", {})
        result = json.loads(str(raw_result))
        assert "error" in result
        assert "Unknown tool" in result["error"]

    async def test_call_tool_exception_returns_error(self, server: APIMCPServer) -> None:
        raw_result = await server.call_tool(
            "api_request",
            {"method": "GET"},  # Missing required 'url' - but api_request will receive it as None
        )
        result = json.loads(str(raw_result))
        assert "error" in result

    async def test_list_resources_returns_empty_list(self, server: APIMCPServer) -> None:
        resources = await server.list_resources()
        assert resources == []

    async def test_health_check_returns_true_when_healthy(self, server: APIMCPServer) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.api_server.server.httpx.AsyncClient", return_value=mock_client):
            result = await server.health_check()

        assert result is True

    async def test_health_check_returns_false_when_unhealthy(self, server: APIMCPServer) -> None:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(side_effect=Exception("connection failed"))

        with patch("testagent.mcp_servers.api_server.server.httpx.AsyncClient", return_value=mock_client):
            result = await server.health_check()

        assert result is False

    async def test_health_check_returns_false_on_non_200(self, server: APIMCPServer) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("testagent.mcp_servers.api_server.server.httpx.AsyncClient", return_value=mock_client):
            result = await server.health_check()

        assert result is False
