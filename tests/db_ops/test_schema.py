"""Tests for testagent.db_ops.schema — SchemaInspector, ColumnInfo, TableInfo."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from testagent.db_ops.errors import SchemaInspectionError
from testagent.db_ops.schema import ColumnInfo, SchemaInspector, TableInfo


# ---------------------------------------------------------------------------
# ColumnInfo
# ---------------------------------------------------------------------------


class TestColumnInfo:
    def test_defaults(self):
        col = ColumnInfo(name="id", type_str="INT")
        assert col.name == "id"
        assert col.type_str == "INT"
        assert col.nullable is True
        assert col.default is None
        assert col.is_primary_key is False
        assert col.comment == ""

    def test_full_init(self):
        col = ColumnInfo(
            name="id",
            type_str="BIGINT",
            nullable=False,
            default="0",
            is_primary_key=True,
            comment="primary key",
        )
        assert col.nullable is False
        assert col.default == "0"
        assert col.is_primary_key is True
        assert col.comment == "primary key"

    def test_to_dict(self):
        col = ColumnInfo(name="name", type_str="VARCHAR(255)", nullable=False)
        d = col.to_dict()
        assert d == {
            "name": "name",
            "type": "VARCHAR(255)",
            "nullable": False,
            "default": None,
            "is_primary_key": False,
            "comment": "",
        }


# ---------------------------------------------------------------------------
# TableInfo
# ---------------------------------------------------------------------------


class TestTableInfo:
    def test_basic(self):
        cols = [ColumnInfo(name="id", type_str="INT")]
        table = TableInfo(name="users", columns=cols, row_count=10)
        assert table.name == "users"
        assert len(table.columns) == 1
        assert table.row_count == 10

    def test_to_dict(self):
        cols = [
            ColumnInfo(name="id", type_str="INT", is_primary_key=True),
            ColumnInfo(name="name", type_str="VARCHAR(100)"),
        ]
        table = TableInfo(name="users", columns=cols, row_count=5)
        d = table.to_dict()
        assert d["name"] == "users"
        assert d["row_count"] == 5
        assert len(d["columns"]) == 2
        assert d["columns"][0]["is_primary_key"] is True

    def test_default_row_count(self):
        table = TableInfo(name="t", columns=[])
        assert table.row_count == 0


# ---------------------------------------------------------------------------
# SchemaInspector
# ---------------------------------------------------------------------------


def _make_mock_conn(dialect_name: str, rows: list | None = None):
    """Helper to create a mock AsyncConnection with dialect and execute result."""
    conn = AsyncMock()
    conn.dialect = MagicMock()
    conn.dialect.name = dialect_name
    if rows is not None:
        mock_result = MagicMock()
        mock_result.fetchall.return_value = rows
        conn.execute.return_value = mock_result
    return conn


class TestSchemaInspectorGetTables:
    @pytest.mark.asyncio
    async def test_mysql_tables(self):
        conn_mgr = AsyncMock()
        conn = _make_mock_conn("mysql", [("users",), ("orders",)])
        conn_mgr.get_connection.return_value = conn

        inspector = SchemaInspector(conn_mgr)
        tables = await inspector.get_tables("mysql://host/db")
        assert tables == ["users", "orders"]

    @pytest.mark.asyncio
    async def test_postgresql_tables(self):
        conn_mgr = AsyncMock()
        conn = _make_mock_conn("postgresql", [("users",), ("products",)])
        conn_mgr.get_connection.return_value = conn

        inspector = SchemaInspector(conn_mgr)
        tables = await inspector.get_tables("postgresql://host/db")
        assert tables == ["users", "products"]

    @pytest.mark.asyncio
    async def test_sqlite_tables(self):
        conn_mgr = AsyncMock()
        conn = _make_mock_conn("sqlite", [("users",), ("settings",)])
        conn_mgr.get_connection.return_value = conn

        inspector = SchemaInspector(conn_mgr)
        tables = await inspector.get_tables("sqlite:///tmp/test.db")
        assert tables == ["users", "settings"]

    @pytest.mark.asyncio
    async def test_unsupported_dialect(self):
        conn_mgr = AsyncMock()
        conn = _make_mock_conn("oracle")
        conn_mgr.get_connection.return_value = conn

        inspector = SchemaInspector(conn_mgr)
        with pytest.raises(SchemaInspectionError) as exc_info:
            await inspector.get_tables("oracle://host/db")
        assert "UNSUPPORTED_DIALECT" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_connection_error_wrapped(self):
        conn_mgr = AsyncMock()
        conn_mgr.get_connection.side_effect = RuntimeError("connection refused")

        inspector = SchemaInspector(conn_mgr)
        with pytest.raises(SchemaInspectionError) as exc_info:
            await inspector.get_tables("mysql://host/db")
        assert "TABLES_QUERY_FAILED" in str(exc_info.value)


class TestSchemaInspectorGetColumns:
    @pytest.mark.asyncio
    async def test_mysql_columns(self):
        conn_mgr = AsyncMock()
        rows = [
            ("id", "int", "NO", None, "PRI", "primary key"),
            ("name", "varchar(100)", "YES", None, "", ""),
        ]
        conn = _make_mock_conn("mysql", rows)
        conn_mgr.get_connection.return_value = conn

        inspector = SchemaInspector(conn_mgr)
        cols = await inspector.get_columns("mysql://host/db", "users")

        assert len(cols) == 2
        assert cols[0].name == "id"
        assert cols[0].is_primary_key is True
        assert cols[0].nullable is False
        assert cols[1].name == "name"
        assert cols[1].nullable is True

    @pytest.mark.asyncio
    async def test_postgresql_columns(self):
        conn_mgr = AsyncMock()
        rows = [
            ("id", "integer", "NO", "nextval('seq')", True),
            ("email", "character varying", "YES", None, False),
        ]
        conn = _make_mock_conn("postgresql", rows)
        conn_mgr.get_connection.return_value = conn

        inspector = SchemaInspector(conn_mgr)
        cols = await inspector.get_columns("postgresql://host/db", "users")

        assert len(cols) == 2
        assert cols[0].is_primary_key is True
        assert cols[1].is_primary_key is False

    @pytest.mark.asyncio
    async def test_sqlite_columns(self):
        conn_mgr = AsyncMock()
        # SQLite PRAGMA returns: (cid, name, type, notnull, dflt_value, pk)
        rows = [
            (0, "id", "INTEGER", 1, None, 1),
            (1, "name", "TEXT", 0, "''", 0),
        ]
        conn = _make_mock_conn("sqlite", rows)
        conn_mgr.get_connection.return_value = conn

        inspector = SchemaInspector(conn_mgr)
        cols = await inspector.get_columns("sqlite:///tmp/test.db", "users")

        assert len(cols) == 2
        assert cols[0].name == "id"
        assert cols[0].is_primary_key is True
        assert cols[0].nullable is False  # notnull=1 means NOT NULL
        assert cols[1].nullable is True   # notnull=0 means nullable

    @pytest.mark.asyncio
    async def test_unsupported_dialect_for_columns(self):
        conn_mgr = AsyncMock()
        conn = _make_mock_conn("oracle")
        conn_mgr.get_connection.return_value = conn

        inspector = SchemaInspector(conn_mgr)
        with pytest.raises(SchemaInspectionError) as exc_info:
            await inspector.get_columns("oracle://host/db", "users")
        assert "UNSUPPORTED_DIALECT" in str(exc_info.value)


class TestSchemaInspectorGetSampleData:
    @pytest.mark.asyncio
    async def test_sample_data(self):
        conn_mgr = AsyncMock()
        rows = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
        mock_result = MagicMock()
        mock_mappings = MagicMock()
        mock_mappings.all.return_value = rows
        mock_result.mappings.return_value = mock_mappings

        conn = AsyncMock()
        conn.execute.return_value = mock_result
        conn_mgr.get_connection.return_value = conn

        inspector = SchemaInspector(conn_mgr)
        data = await inspector.get_sample_data("mysql://host/db", "users", limit=2)
        assert len(data) == 2
        assert data[0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_sample_data_error(self):
        conn_mgr = AsyncMock()
        conn = AsyncMock()
        conn.execute.side_effect = RuntimeError("table not found")
        conn_mgr.get_connection.return_value = conn

        inspector = SchemaInspector(conn_mgr)
        with pytest.raises(SchemaInspectionError) as exc_info:
            await inspector.get_sample_data("mysql://host/db", "nonexistent")
        assert "SAMPLE_DATA_FAILED" in str(exc_info.value)


class TestSchemaInspectorGetFullSchema:
    @pytest.mark.asyncio
    async def test_full_schema_includes_has_is_test(self):
        conn_mgr = AsyncMock()
        inspector = SchemaInspector(conn_mgr)

        # Mock get_tables and get_columns
        inspector.get_tables = AsyncMock(return_value=["users"])
        col_with_flag = ColumnInfo(name="is_test", type_str="TINYINT")
        col_id = ColumnInfo(name="id", type_str="INT")
        inspector.get_columns = AsyncMock(return_value=[col_id, col_with_flag])

        schema = await inspector.get_full_schema("mysql://host/db")
        assert "users" in schema
        assert schema["users"]["has_is_test"] is True

    @pytest.mark.asyncio
    async def test_full_schema_without_is_test(self):
        conn_mgr = AsyncMock()
        inspector = SchemaInspector(conn_mgr)

        inspector.get_tables = AsyncMock(return_value=["orders"])
        col_id = ColumnInfo(name="id", type_str="INT")
        inspector.get_columns = AsyncMock(return_value=[col_id])

        schema = await inspector.get_full_schema("mysql://host/db")
        assert schema["orders"]["has_is_test"] is False


class TestSchemaInspectorFormatSchema:
    def test_format_basic(self):
        inspector = SchemaInspector(AsyncMock())
        schema = {
            "users": {
                "columns": [
                    {
                        "name": "id",
                        "type": "INT",
                        "nullable": False,
                        "default": None,
                        "is_primary_key": True,
                        "comment": "",
                    },
                    {
                        "name": "name",
                        "type": "VARCHAR(100)",
                        "nullable": True,
                        "default": None,
                        "is_primary_key": False,
                        "comment": "",
                    },
                ],
                "has_is_test": False,
            }
        }
        result = inspector.format_schema_for_prompt(schema)
        assert "CREATE TABLE users" in result
        assert "id INT NOT NULL PRIMARY KEY" in result
        assert "name VARCHAR(100)" in result
        assert "NOT NULL" not in result.split("name")[1].split("\n")[0]

    def test_format_with_default(self):
        inspector = SchemaInspector(AsyncMock())
        schema = {
            "t": {
                "columns": [
                    {
                        "name": "status",
                        "type": "INT",
                        "nullable": True,
                        "default": "0",
                        "is_primary_key": False,
                        "comment": "",
                    }
                ],
                "has_is_test": False,
            }
        }
        result = inspector.format_schema_for_prompt(schema)
        assert "DEFAULT 0" in result

    def test_format_empty_schema(self):
        inspector = SchemaInspector(AsyncMock())
        result = inspector.format_schema_for_prompt({})
        assert result == ""
