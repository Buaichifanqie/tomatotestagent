from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.db_toolkit.errors import EnvironmentViolationError
from testagent.db_toolkit.models import Environment, DbEnv
from testagent.db_toolkit.tools import (
    DB_TOOL_DEFINITIONS,
    ToolkitState,
    handle_db_cleanup,
    handle_db_execute,
    handle_db_inspect,
    handle_db_query,
)


def _make_state(env_level: Environment = Environment.TEST) -> ToolkitState:
    env = DbEnv(
        level=env_level,
        connection_url="sqlite+aiosqlite://",
        detected_by="config" if env_level == Environment.TEST else "default",
    )
    return ToolkitState(
        env=env,
        conn_manager=AsyncMock(),
        llm=AsyncMock(),
    )


class TestDbInspect:
    @pytest.mark.asyncio
    async def test_returns_schema(self):
        state = _make_state()
        mock_inspector = AsyncMock()
        mock_inspector.get_full_schema.return_value = [
            MagicMock(name="users", to_dict=lambda: {"name": "users", "columns": []}),
        ]

        with patch("testagent.db_toolkit.tools.SchemaInspector", return_value=mock_inspector):
            result = await handle_db_inspect(state, {"connection_url": "sqlite+aiosqlite://"})

        assert "tables" in result
        assert len(result["tables"]) == 1


class TestDbQuery:
    @pytest.mark.asyncio
    async def test_select_allowed_in_prod(self):
        state = _make_state(Environment.PRODUCTION)
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.returns_rows = True
        mock_result.mappings.return_value.all.return_value = [{"id": 1}]
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
        state.conn_manager.get_engine.return_value = mock_engine

        result = await handle_db_query(state, {
            "connection_url": "sqlite+aiosqlite://",
            "sql": "SELECT * FROM users",
        })
        assert result["success"] is True
        assert result["data"] == [{"id": 1}]


class TestDbExecute:
    @pytest.mark.asyncio
    async def test_blocked_in_prod(self):
        state = _make_state(Environment.PRODUCTION)
        with pytest.raises(EnvironmentViolationError):
            await handle_db_execute(state, {
                "connection_url": "sqlite+aiosqlite://",
                "sql": "INSERT INTO users (name) VALUES ('test')",
                "confirm": True,
            })

    @pytest.mark.asyncio
    async def test_preview_without_confirm(self):
        state = _make_state()
        result = await handle_db_execute(state, {
            "connection_url": "sqlite+aiosqlite://",
            "sql": "INSERT INTO users (name) VALUES (:name)",
            "params": {"name": "test_user"},
            "confirm": False,
        })
        assert result["preview"] is True
        assert "INSERT INTO users" in result["sql"]


class TestDbCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_with_records(self):
        state = _make_state()
        state.cleanup_tracker.record_insert("users", inserted_ids=[1, 2])

        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.returns_rows = False
        mock_result.rowcount = 2
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)
        state.conn_manager.get_engine.return_value = mock_engine

        result = await handle_db_cleanup(state, {"connection_url": "sqlite+aiosqlite://"})
        assert result["cleaned"] == 1
        assert state.cleanup_tracker.get_records() == []


class TestToolDefinitions:
    def test_has_four_tools(self):
        assert len(DB_TOOL_DEFINITIONS) == 4

    def test_tool_names(self):
        names = {t["name"] for t in DB_TOOL_DEFINITIONS}
        assert names == {"db_inspect", "db_query", "db_execute", "db_cleanup"}

    def test_each_has_required_keys(self):
        for tool in DB_TOOL_DEFINITIONS:
            assert "name" in tool
            assert "description" in tool
            assert "parameters" in tool
            assert "properties" in tool["parameters"]
