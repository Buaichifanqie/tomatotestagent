from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from testagent.common.logging import get_logger
from testagent.db_toolkit.connection import ConnectionManager
from testagent.db_toolkit.errors import SchemaInspectionError

logger = get_logger(__name__)


@dataclass
class ColumnInfo:
    name: str
    type_str: str
    nullable: bool = True
    default: str | None = None
    is_primary_key: bool = False
    comment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type_str,
            "nullable": self.nullable,
            "default": self.default,
            "is_primary_key": self.is_primary_key,
            "comment": self.comment,
        }


@dataclass
class TableInfo:
    name: str
    columns: list[ColumnInfo] = field(default_factory=list)
    row_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "columns": [c.to_dict() for c in self.columns],
            "row_count": self.row_count,
        }


class SchemaInspector:
    """Inspects the schema of the user's application database."""

    def __init__(self, conn_manager: ConnectionManager) -> None:
        self._conn_manager = conn_manager

    async def get_tables(self, connection_url: str, dialect: str | None = None) -> list[str]:
        engine = await self._conn_manager.get_engine(connection_url)
        async with engine.connect() as conn:
            d = dialect or conn.dialect.name
            if d == "mysql":
                return await self._get_tables_mysql(conn)
            elif d == "postgresql":
                return await self._get_tables_postgresql(conn)
            elif d == "sqlite":
                return await self._get_tables_sqlite(conn)
            else:
                raise SchemaInspectionError(f"Unsupported dialect: {d}", code="UNSUPPORTED_DIALECT")

    async def get_columns(self, connection_url: str, table: str, dialect: str | None = None) -> list[ColumnInfo]:
        engine = await self._conn_manager.get_engine(connection_url)
        async with engine.connect() as conn:
            d = dialect or conn.dialect.name
            if d == "mysql":
                return await self._get_columns_mysql(conn, table)
            elif d == "postgresql":
                return await self._get_columns_postgresql(conn, table)
            elif d == "sqlite":
                return await self._get_columns_sqlite(conn, table)
            else:
                raise SchemaInspectionError(f"Unsupported dialect: {d}", code="UNSUPPORTED_DIALECT")

    async def get_sample_data(
        self, connection_url: str, table: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        engine = await self._conn_manager.get_engine(connection_url)
        async with engine.connect() as conn:
            result = await conn.execute(text(f"SELECT * FROM {table} LIMIT :limit"), {"limit": limit})
            rows = result.mappings().all()
            return [dict(row) for row in rows]

    async def get_full_schema(self, connection_url: str) -> list[TableInfo]:
        tables = await self.get_tables(connection_url)
        result: list[TableInfo] = []
        for table in tables:
            columns = await self.get_columns(connection_url, table)
            result.append(TableInfo(name=table, columns=columns))
        return result

    @staticmethod
    def format_for_prompt(tables: list[TableInfo]) -> str:
        lines: list[str] = []
        for t in tables:
            col_defs: list[str] = []
            for c in t.columns:
                parts = [f"  {c.name} {c.type_str}"]
                if not c.nullable:
                    parts.append("NOT NULL")
                if c.default is not None:
                    parts.append(f"DEFAULT {c.default}")
                if c.is_primary_key:
                    parts.append("PRIMARY KEY")
                col_defs.append(" ".join(parts))
            lines.append(f"CREATE TABLE {t.name} (")
            lines.append(",\n".join(col_defs))
            lines.append(");")
            lines.append("")
        return "\n".join(lines)

    # -- MySQL ----------------------------------------------------------------

    async def _get_tables_mysql(self, conn: Any) -> list[str]:
        result = await conn.execute(text("SHOW TABLES"))
        return [row[0] for row in result.fetchall()]

    async def _get_columns_mysql(self, conn: Any, table: str) -> list[ColumnInfo]:
        result = await conn.execute(
            text(
                "SELECT COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT, "
                "COLUMN_KEY, COLUMN_COMMENT "
                "FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_NAME = :table ORDER BY ORDINAL_POSITION"
            ),
            {"table": table},
        )
        return [
            ColumnInfo(
                name=row[0], type_str=row[1], nullable=row[2] == "YES",
                default=str(row[3]) if row[3] is not None else None,
                is_primary_key=row[4] == "PRI", comment=row[5] or "",
            )
            for row in result.fetchall()
        ]

    # -- PostgreSQL -----------------------------------------------------------

    async def _get_tables_postgresql(self, conn: Any) -> list[str]:
        result = await conn.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"
            )
        )
        return [row[0] for row in result.fetchall()]

    async def _get_columns_postgresql(self, conn: Any, table: str) -> list[ColumnInfo]:
        result = await conn.execute(
            text(
                "SELECT c.column_name, c.data_type, c.is_nullable, c.column_default, "
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
        return [
            ColumnInfo(
                name=row[0], type_str=row[1], nullable=row[2] == "YES",
                default=str(row[3]) if row[3] is not None else None,
                is_primary_key=bool(row[4]),
            )
            for row in result.fetchall()
        ]

    # -- SQLite ---------------------------------------------------------------

    async def _get_tables_sqlite(self, conn: Any) -> list[str]:
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
        )
        return [row[0] for row in result.fetchall()]

    async def _get_columns_sqlite(self, conn: Any, table: str) -> list[ColumnInfo]:
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        return [
            ColumnInfo(
                name=row[1], type_str=row[2], nullable=row[3] == 0,
                default=str(row[4]) if row[4] is not None else None,
                is_primary_key=row[5] == 1,
            )
            for row in result.fetchall()
        ]
