from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.mcp_servers.database_server.server import DatabaseMCPServer
from testagent.mcp_servers.database_server.tools import db_cleanup, db_query, db_seed


@pytest.fixture()
def server() -> DatabaseMCPServer:
    return DatabaseMCPServer()


@pytest.fixture()
def mock_engine() -> MagicMock:
    engine = MagicMock()
    engine.connect = MagicMock()
    engine.dispose = AsyncMock()
    return engine


def _setup_conn_context(conn_mock: AsyncMock) -> AsyncMock:
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=conn_mock)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


class TestDbQuery:
    async def test_query_returns_columns_and_rows(self, mock_engine: MagicMock) -> None:
        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_result.keys.return_value = ["id", "name", "email"]
        mock_result.fetchmany.return_value = [
            (1, "Alice", "alice@test.com"),
            (2, "Bob", "bob@test.com"),
        ]
        mock_result.rowcount = 2

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_engine.connect.return_value = _setup_conn_context(mock_conn)

        with patch("testagent.mcp_servers.database_server.tools.create_async_engine", return_value=mock_engine):
            result = await db_query(
                database_url="sqlite+aiosqlite:///:memory:",
                sql="SELECT * FROM users",
            )

        assert result["success"] is True
        assert result["columns"] == ["id", "name", "email"]
        assert len(result["rows"]) == 2
        assert result["rows"][0]["id"] == 1
        assert result["rows"][0]["name"] == "Alice"
        assert result["row_count"] == 2
        assert result["truncated"] is False

    async def test_query_respects_max_rows(self, mock_engine: MagicMock) -> None:
        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_result.keys.return_value = ["id"]
        mock_result.fetchmany.return_value = [(1,), (2,)]
        mock_result.rowcount = 10

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_engine.connect.return_value = _setup_conn_context(mock_conn)

        with patch("testagent.mcp_servers.database_server.tools.create_async_engine", return_value=mock_engine):
            result = await db_query(
                database_url="sqlite+aiosqlite:///:memory:",
                sql="SELECT * FROM users",
                max_rows=2,
            )

        assert result["row_count"] == 2
        assert result["truncated"] is True

    async def test_query_non_select_statement(self, mock_engine: MagicMock) -> None:
        mock_result = MagicMock()
        mock_result.returns_rows = False
        mock_result.rowcount = 3

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_conn.commit = AsyncMock()
        mock_engine.connect.return_value = _setup_conn_context(mock_conn)

        with patch("testagent.mcp_servers.database_server.tools.create_async_engine", return_value=mock_engine):
            result = await db_query(
                database_url="sqlite+aiosqlite:///:memory:",
                sql="DELETE FROM users WHERE id=1",
            )

        assert result["success"] is True
        assert result["row_count"] == 3

    async def test_query_with_params(self, mock_engine: MagicMock) -> None:
        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_result.keys.return_value = ["id", "name"]
        mock_result.fetchmany.return_value = [(1, "Alice")]
        mock_result.rowcount = 1

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_engine.connect.return_value = _setup_conn_context(mock_conn)

        with patch("testagent.mcp_servers.database_server.tools.create_async_engine", return_value=mock_engine):
            result = await db_query(
                database_url="sqlite+aiosqlite:///:memory:",
                sql="SELECT * FROM users WHERE id = :uid",
                params={"uid": 1},
            )

        assert result["success"] is True

    async def test_query_sql_error_returns_error(self, mock_engine: MagicMock) -> None:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=Exception("syntax error"))
        mock_engine.connect.return_value = _setup_conn_context(mock_conn)

        with patch("testagent.mcp_servers.database_server.tools.create_async_engine", return_value=mock_engine):
            result = await db_query(
                database_url="sqlite+aiosqlite:///:memory:",
                sql="INVALID SQL",
            )

        assert "error" in result
        assert "syntax error" in result["error"]

    async def test_query_datetime_values_serialized(self, mock_engine: MagicMock) -> None:
        mock_dt = datetime(2024, 1, 15, 10, 30, 0)

        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_result.keys.return_value = ["created_at"]
        mock_result.fetchmany.return_value = [(mock_dt,)]
        mock_result.rowcount = 1

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_engine.connect.return_value = _setup_conn_context(mock_conn)

        with patch("testagent.mcp_servers.database_server.tools.create_async_engine", return_value=mock_engine):
            result = await db_query(
                database_url="sqlite+aiosqlite:///:memory:",
                sql="SELECT created_at FROM users",
            )

        assert result["rows"][0]["created_at"] == "2024-01-15T10:30:00"


class TestDbSeed:
    async def test_seed_inserts_data(self, mock_engine: MagicMock) -> None:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.commit = AsyncMock()
        mock_engine.connect.return_value = _setup_conn_context(mock_conn)

        with patch("testagent.mcp_servers.database_server.tools.create_async_engine", return_value=mock_engine):
            result = await db_seed(
                database_url="sqlite+aiosqlite:///:memory:",
                table="users",
                data=[{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}],
            )

        assert result["success"] is True
        assert result["inserted_count"] == 2
        assert result["table"] == "users"

    async def test_seed_with_truncate_first(self, mock_engine: MagicMock) -> None:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.commit = AsyncMock()
        mock_engine.connect.return_value = _setup_conn_context(mock_conn)

        with patch("testagent.mcp_servers.database_server.tools.create_async_engine", return_value=mock_engine):
            result = await db_seed(
                database_url="sqlite+aiosqlite:///:memory:",
                table="users",
                data=[{"id": 1, "name": "Alice"}],
                truncate_first=True,
            )

        assert result["success"] is True
        assert result["inserted_count"] == 1

    async def test_seed_empty_data_skips_insert(self, mock_engine: MagicMock) -> None:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.commit = AsyncMock()
        mock_engine.connect.return_value = _setup_conn_context(mock_conn)

        with patch("testagent.mcp_servers.database_server.tools.create_async_engine", return_value=mock_engine):
            result = await db_seed(
                database_url="sqlite+aiosqlite:///:memory:",
                table="users",
                data=[],
            )

        assert result["success"] is True
        assert result["inserted_count"] == 0

    async def test_seed_sql_error_returns_error(self, mock_engine: MagicMock) -> None:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=Exception("table does not exist"))
        mock_engine.connect.return_value = _setup_conn_context(mock_conn)

        with patch("testagent.mcp_servers.database_server.tools.create_async_engine", return_value=mock_engine):
            result = await db_seed(
                database_url="sqlite+aiosqlite:///:memory:",
                table="nonexistent",
                data=[{"id": 1}],
            )

        assert "error" in result


class TestDbCleanup:
    async def test_cleanup_specific_tables(self, mock_engine: MagicMock) -> None:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.commit = AsyncMock()
        mock_engine.connect.return_value = _setup_conn_context(mock_conn)

        with patch("testagent.mcp_servers.database_server.tools.create_async_engine", return_value=mock_engine):
            result = await db_cleanup(
                database_url="sqlite+aiosqlite:///:memory:",
                tables=["users", "orders"],
            )

        assert result["success"] is True
        assert "users" in result["cleaned_tables"]
        assert "orders" in result["cleaned_tables"]

    async def test_cleanup_error_returns_error(self, mock_engine: MagicMock) -> None:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=Exception("permission denied"))
        mock_engine.connect.return_value = _setup_conn_context(mock_conn)

        with patch("testagent.mcp_servers.database_server.tools.create_async_engine", return_value=mock_engine):
            result = await db_cleanup(
                database_url="sqlite+aiosqlite:///:memory:",
                tables=["users"],
            )

        assert "error" in result


class TestDatabaseMCPServer:
    def test_server_name_is_database_server(self, server: DatabaseMCPServer) -> None:
        assert server.server_name == "database_server"

    async def test_list_tools_returns_three_tools(self, server: DatabaseMCPServer) -> None:
        tools = await server.list_tools()
        assert len(tools) == 3
        tool_names = {t["name"] for t in tools}
        assert tool_names == {"db_query", "db_seed", "db_cleanup"}

    async def test_list_tools_input_schemas_have_required_fields(self, server: DatabaseMCPServer) -> None:
        tools = await server.list_tools()
        for tool in tools:
            assert "name" in tool
            assert "description" in tool
            assert "inputSchema" in tool
            assert "required" in tool["inputSchema"]

    async def test_call_tool_db_query_dispatches(self, server: DatabaseMCPServer, mock_engine: MagicMock) -> None:
        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_result.keys.return_value = ["id"]
        mock_result.fetchmany.return_value = [(1,)]
        mock_result.rowcount = 1

        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(return_value=mock_result)
        mock_engine.connect.return_value = _setup_conn_context(mock_conn)

        with patch("testagent.mcp_servers.database_server.tools.create_async_engine", return_value=mock_engine):
            raw_result = await server.call_tool(
                "db_query",
                {
                    "database_url": "sqlite+aiosqlite:///:memory:",
                    "sql": "SELECT * FROM users",
                },
            )

        import json

        result = json.loads(str(raw_result))
        assert result["success"] is True
        assert result["columns"] == ["id"]
        assert result["row_count"] == 1

    async def test_call_tool_db_seed_dispatches(self, server: DatabaseMCPServer, mock_engine: MagicMock) -> None:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.commit = AsyncMock()
        mock_engine.connect.return_value = _setup_conn_context(mock_conn)

        with patch("testagent.mcp_servers.database_server.tools.create_async_engine", return_value=mock_engine):
            raw_result = await server.call_tool(
                "db_seed",
                {
                    "database_url": "sqlite+aiosqlite:///:memory:",
                    "table": "users",
                    "data": [{"id": 1, "name": "Alice"}],
                },
            )

        import json

        result = json.loads(str(raw_result))
        assert result["success"] is True
        assert result["inserted_count"] == 1

    async def test_call_tool_db_cleanup_dispatches(self, server: DatabaseMCPServer, mock_engine: MagicMock) -> None:
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock()
        mock_conn.commit = AsyncMock()
        mock_engine.connect.return_value = _setup_conn_context(mock_conn)

        with patch("testagent.mcp_servers.database_server.tools.create_async_engine", return_value=mock_engine):
            raw_result = await server.call_tool(
                "db_cleanup",
                {
                    "database_url": "sqlite+aiosqlite:///:memory:",
                    "tables": ["users"],
                },
            )

        import json

        result = json.loads(str(raw_result))
        assert result["success"] is True

    async def test_call_tool_unknown_tool_returns_error(self, server: DatabaseMCPServer) -> None:
        raw_result = await server.call_tool("unknown_tool", {})
        import json

        result = json.loads(str(raw_result))
        assert "error" in result
        assert "Unknown tool" in result["error"]

    async def test_list_resources_returns_db_schema_and_data_dictionary(self, server: DatabaseMCPServer) -> None:
        resources = await server.list_resources()
        assert len(resources) == 2
        uris = {r["uri"] for r in resources}
        assert uris == {"db://schema", "db://data_dictionary"}

    async def test_list_resources_have_required_fields(self, server: DatabaseMCPServer) -> None:
        resources = await server.list_resources()
        for resource in resources:
            assert "uri" in resource
            assert "name" in resource
            assert "mimeType" in resource
            assert "description" in resource

    async def test_health_check_returns_true(self, server: DatabaseMCPServer) -> None:
        result = await server.health_check()
        assert result is True
