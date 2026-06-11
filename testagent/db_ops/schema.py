from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from testagent.common.logging import get_logger
from testagent.db_ops.connection import ConnectionManager
from testagent.db_ops.errors import SchemaInspectionError

logger = get_logger(__name__)


class ColumnInfo:
    """Metadata for a single database column."""

    __slots__ = ("name", "type_str", "nullable", "default", "is_primary_key", "comment")

    def __init__(
        self,
        name: str,
        type_str: str,
        nullable: bool = True,
        default: str | None = None,
        is_primary_key: bool = False,
        comment: str = "",
    ) -> None:
        self.name = name
        self.type_str = type_str
        self.nullable = nullable
        self.default = default
        self.is_primary_key = is_primary_key
        self.comment = comment

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type_str,
            "nullable": self.nullable,
            "default": self.default,
            "is_primary_key": self.is_primary_key,
            "comment": self.comment,
        }


class TableInfo:
    """Metadata for a single database table."""

    __slots__ = ("name", "columns", "row_count")

    def __init__(self, name: str, columns: list[ColumnInfo], row_count: int = 0) -> None:
        self.name = name
        self.columns = columns
        self.row_count = row_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "columns": [c.to_dict() for c in self.columns],
            "row_count": self.row_count,
        }


class SchemaInspector:
    """Inspects the schema of the user's application database.

    Supports MySQL, PostgreSQL, and SQLite via dialect-specific queries.
    """

    def __init__(self, conn_manager: ConnectionManager) -> None:
        self._conn_manager = conn_manager

    async def get_tables(self, connection_url: str) -> list[str]:
        """List all table names in the database."""
        try:
            conn = await self._conn_manager.get_connection(connection_url)
        except Exception as exc:
            raise SchemaInspectionError(
                f"Failed to connect for table listing: {exc}",
                code="TABLES_QUERY_FAILED",
            ) from exc
        try:
            dialect = conn.dialect.name
            if dialect == "mysql":
                return await self._get_tables_mysql(conn)
            elif dialect == "postgresql":
                return await self._get_tables_postgresql(conn)
            elif dialect == "sqlite":
                return await self._get_tables_sqlite(conn)
            else:
                raise SchemaInspectionError(
                    f"Unsupported dialect: {dialect}",
                    code="UNSUPPORTED_DIALECT",
                )
        except SchemaInspectionError:
            raise
        except Exception as exc:
            raise SchemaInspectionError(
                f"Failed to list tables: {exc}",
                code="TABLES_QUERY_FAILED",
            ) from exc
        finally:
            await conn.close()

    async def get_columns(self, connection_url: str, table: str) -> list[ColumnInfo]:
        """Get column metadata for a specific table."""
        conn = await self._conn_manager.get_connection(connection_url)
        try:
            dialect = conn.dialect.name
            if dialect == "mysql":
                return await self._get_columns_mysql(conn, table)
            elif dialect == "postgresql":
                return await self._get_columns_postgresql(conn, table)
            elif dialect == "sqlite":
                return await self._get_columns_sqlite(conn, table)
            else:
                raise SchemaInspectionError(
                    f"Unsupported dialect: {dialect}",
                    code="UNSUPPORTED_DIALECT",
                )
        except SchemaInspectionError:
            raise
        except Exception as exc:
            raise SchemaInspectionError(
                f"Failed to get columns for {table}: {exc}",
                code="COLUMNS_QUERY_FAILED",
            ) from exc
        finally:
            await conn.close()

    async def get_sample_data(
        self, connection_url: str, table: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        """Fetch sample rows from a table."""
        conn = await self._conn_manager.get_connection(connection_url)
        try:
            query = text(f"SELECT * FROM {table} LIMIT :limit")
            result = await conn.execute(query, {"limit": limit})
            rows = result.mappings().all()
            return [dict(row) for row in rows]
        except Exception as exc:
            raise SchemaInspectionError(
                f"Failed to sample data from {table}: {exc}",
                code="SAMPLE_DATA_FAILED",
            ) from exc
        finally:
            await conn.close()

    async def get_full_schema(self, connection_url: str) -> dict[str, Any]:
        """Get complete schema info: all tables with columns and sample data."""
        tables = await self.get_tables(connection_url)
        schema: dict[str, Any] = {}
        for table in tables:
            columns = await self.get_columns(connection_url, table)
            schema[table] = {
                "columns": [c.to_dict() for c in columns],
                "has_is_test": any(c.name == "is_test" for c in columns),
            }
        return schema

    def format_schema_for_prompt(self, schema: dict[str, Any]) -> str:
        """Format schema dict as SQL CREATE TABLE statements for LLM prompts."""
        lines: list[str] = []
        for table, info in schema.items():
            cols = info["columns"]
            col_defs: list[str] = []
            for c in cols:
                parts = [f"  {c['name']} {c['type']}"]
                if not c["nullable"]:
                    parts.append("NOT NULL")
                if c["default"] is not None:
                    parts.append(f"DEFAULT {c['default']}")
                if c["is_primary_key"]:
                    parts.append("PRIMARY KEY")
                col_defs.append(" ".join(parts))
            lines.append(f"CREATE TABLE {table} (")
            lines.append(",\n".join(col_defs))
            lines.append(");")
            lines.append("")
        return "\n".join(lines)

    # -- MySQL-specific queries ------------------------------------------------

    async def _get_tables_mysql(self, conn: AsyncConnection) -> list[str]:
        result = await conn.execute(text("SHOW TABLES"))
        return [row[0] for row in result.fetchall()]

    async def _get_columns_mysql(self, conn: AsyncConnection, table: str) -> list[ColumnInfo]:
        result = await conn.execute(
            text(
                "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, "
                "COLUMN_KEY, COLUMN_COMMENT "
                "FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME = :table ORDER BY ORDINAL_POSITION"
            ),
            {"table": table},
        )
        columns: list[ColumnInfo] = []
        for row in result.fetchall():
            columns.append(
                ColumnInfo(
                    name=row[0],
                    type_str=row[1],
                    nullable=row[2] == "YES",
                    default=str(row[3]) if row[3] is not None else None,
                    is_primary_key=row[4] == "PRI",
                    comment=row[5] or "",
                )
            )
        return columns

    # -- PostgreSQL-specific queries -------------------------------------------

    async def _get_tables_postgresql(self, conn: AsyncConnection) -> list[str]:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            )
        )
        return [row[0] for row in result.fetchall()]

    async def _get_columns_postgresql(
        self, conn: AsyncConnection, table: str
    ) -> list[ColumnInfo]:
        result = await conn.execute(
            text(
                "SELECT c.column_name, c.data_type, c.is_nullable, "
                "c.column_default, "
                "CASE WHEN pk.column_name IS NOT NULL THEN true ELSE false END AS is_pk "
                "FROM information_schema.columns c "
                "LEFT JOIN ("
                "  SELECT ku.column_name FROM information_schema.table_constraints tc "
                "  JOIN information_schema.key_column_usage ku "
                "  ON tc.constraint_name = ku.constraint_name "
                "  WHERE tc.table_name = :table AND tc.constraint_type = 'PRIMARY KEY'"
                ") pk ON c.column_name = pk.column_name "
                "WHERE c.table_name = :table ORDER BY c.ordinal_position"
            ),
            {"table": table},
        )
        columns: list[ColumnInfo] = []
        for row in result.fetchall():
            columns.append(
                ColumnInfo(
                    name=row[0],
                    type_str=row[1],
                    nullable=row[2] == "YES",
                    default=str(row[3]) if row[3] is not None else None,
                    is_primary_key=bool(row[4]),
                )
            )
        return columns

    # -- SQLite-specific queries -----------------------------------------------

    async def _get_tables_sqlite(self, conn: AsyncConnection) -> list[str]:
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        )
        return [row[0] for row in result.fetchall()]

    async def _get_columns_sqlite(self, conn: AsyncConnection, table: str) -> list[ColumnInfo]:
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        columns: list[ColumnInfo] = []
        for row in result.fetchall():
            columns.append(
                ColumnInfo(
                    name=row[1],
                    type_str=row[2],
                    nullable=row[3] == 0,
                    default=str(row[4]) if row[4] is not None else None,
                    is_primary_key=row[5] == 1,
                )
            )
        return columns
