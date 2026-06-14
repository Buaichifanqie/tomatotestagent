from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from testagent.db_toolkit.schema import ColumnInfo, SchemaInspector, TableInfo


class TestColumnInfo:
    def test_to_dict(self):
        col = ColumnInfo(name="id", type_str="INTEGER", nullable=False, is_primary_key=True)
        d = col.to_dict()
        assert d["name"] == "id"
        assert d["type"] == "INTEGER"
        assert d["nullable"] is False
        assert d["is_primary_key"] is True


class TestTableInfo:
    def test_to_dict(self):
        cols = [ColumnInfo(name="id", type_str="INT"), ColumnInfo(name="name", type_str="VARCHAR")]
        t = TableInfo(name="users", columns=cols, row_count=10)
        d = t.to_dict()
        assert d["name"] == "users"
        assert len(d["columns"]) == 2
        assert d["row_count"] == 10


class TestSchemaInspector:
    @pytest.mark.asyncio
    async def test_get_tables_sqlite(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_conn.dialect = MagicMock(name="sqlite")
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [("users",), ("orders",)]
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_mgr = AsyncMock()
        mock_mgr.get_engine.return_value = mock_engine

        inspector = SchemaInspector(mock_mgr)
        tables = await inspector.get_tables("sqlite:///test.db", dialect="sqlite")
        assert tables == ["users", "orders"]

    @pytest.mark.asyncio
    async def test_get_sample_data(self):
        mock_engine = MagicMock()
        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = [
            {"id": 1, "name": "alice"},
            {"id": 2, "name": "bob"},
        ]
        mock_conn.execute.return_value = mock_result
        mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_mgr = AsyncMock()
        mock_mgr.get_engine.return_value = mock_engine

        inspector = SchemaInspector(mock_mgr)
        data = await inspector.get_sample_data("sqlite:///test.db", "users", limit=2)
        assert len(data) == 2
        assert data[0]["name"] == "alice"

    def test_format_for_prompt(self):
        tables = [
            TableInfo(
                name="users",
                columns=[
                    ColumnInfo(name="id", type_str="INT", nullable=False, is_primary_key=True),
                    ColumnInfo(name="name", type_str="VARCHAR(100)", nullable=True),
                ],
            )
        ]
        prompt = SchemaInspector.format_for_prompt(tables)
        assert "CREATE TABLE users" in prompt
        assert "id INT NOT NULL PRIMARY KEY" in prompt
        assert "name VARCHAR(100)" in prompt
