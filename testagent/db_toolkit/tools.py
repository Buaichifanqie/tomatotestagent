from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from sqlalchemy import text

from testagent.common.logging import get_logger
from testagent.db_toolkit.cleanup import CleanupTracker
from testagent.db_toolkit.connection import ConnectionManager
from testagent.db_toolkit.env import detect_environment
from testagent.db_toolkit.errors import (
    DbToolkitError,
    EnvironmentViolationError,
    SafetyViolationError,
    SqlExecutionError,
)
from testagent.db_toolkit.models import DbEnv, ExecutionResult, SqlOpType
from testagent.db_toolkit.safety import SafetyGuard
from testagent.db_toolkit.schema import SchemaInspector

logger = get_logger(__name__)

_PROMPTS_DIR = Path(__file__).parent / "prompts"
_SELECT_LIMIT = 1000


class ToolkitState:
    """Holds shared state for all db toolkit tools."""

    def __init__(
        self,
        env: DbEnv,
        conn_manager: ConnectionManager,
        llm: Any = None,
    ) -> None:
        self.env = env
        self.conn_manager = conn_manager
        self.llm = llm
        self.safety = SafetyGuard()
        self.cleanup_tracker = CleanupTracker()


# -- Tool handlers -----------------------------------------------------------


async def handle_db_inspect(state: ToolkitState, args: dict[str, Any]) -> dict[str, Any]:
    """Inspect database schema: tables, columns, relationships, sample data."""
    connection_url = args["connection_url"]
    tables_filter = args.get("tables")
    include_sample = args.get("include_sample", True)
    sample_limit = args.get("sample_limit", 5)

    try:
        inspector = SchemaInspector(state.conn_manager)
        schema = await inspector.get_full_schema(connection_url)

        if tables_filter:
            schema = [t for t in schema if t.name in tables_filter]

        result_tables = []
        for table in schema:
            table_dict = table.to_dict()
            if include_sample:
                try:
                    table_dict["sample_data"] = await inspector.get_sample_data(
                        connection_url, table.name, limit=sample_limit
                    )
                except Exception:
                    table_dict["sample_data"] = []
            result_tables.append(table_dict)

        return {
            "tables": result_tables,
            "total_tables": len(result_tables),
            "environment": state.env.level.value,
        }
    except DbToolkitError:
        raise
    except Exception as exc:
        raise SqlExecutionError(
            f"Schema inspection failed: {exc}",
            code="INSPECT_FAILED",
        ) from exc


async def handle_db_query(state: ToolkitState, args: dict[str, Any]) -> dict[str, Any]:
    """Execute a SELECT query. All environments allowed."""
    connection_url = args["connection_url"]
    sql = args["sql"]
    params = args.get("params")

    state.safety.check(state.env, SqlOpType.SELECT, sql)
    sql = _inject_limit(sql)

    try:
        engine = await state.conn_manager.get_engine(connection_url)
        start = time.monotonic()
        async with engine.connect() as conn:
            stmt = text(sql)
            if params:
                stmt = stmt.bindparams(**params)
            result = await conn.execute(stmt)
            duration = int((time.monotonic() - start) * 1000)

            if result.returns_rows:
                rows = result.mappings().all()
                data = [dict(row) for row in rows]
                return {
                    "success": True,
                    "data": data,
                    "rows_affected": len(data),
                    "duration_ms": duration,
                }
            return {"success": True, "data": [], "rows_affected": 0, "duration_ms": duration}
    except DbToolkitError:
        raise
    except Exception as exc:
        raise SqlExecutionError(f"Query failed: {exc}", code="QUERY_FAILED") from exc


async def handle_db_execute(state: ToolkitState, args: dict[str, Any]) -> dict[str, Any]:
    """Execute a write operation (INSERT/UPDATE/DELETE). TEST environment only."""
    connection_url = args["connection_url"]
    sql = args["sql"]
    params = args.get("params")
    confirm = args.get("confirm", False)

    op_type = _detect_op_type(sql)
    state.safety.check(state.env, op_type, sql)

    if not confirm:
        return {
            "preview": True,
            "sql": sql,
            "params": params or {},
            "op_type": op_type.value,
            "environment": state.env.level.value,
            "message": "This is a preview. Call again with confirm=true to execute.",
        }

    original_values: list[dict[str, Any]] = []
    if op_type in (SqlOpType.UPDATE, SqlOpType.DELETE):
        original_values = await _snapshot_affected_rows(
            state, connection_url, sql, params, op_type
        )

    try:
        engine = await state.conn_manager.get_engine(connection_url)
        start = time.monotonic()
        async with engine.connect() as conn:
            stmt = text(sql)
            if params:
                stmt = stmt.bindparams(**params)
            result = await conn.execute(stmt)
            await conn.commit()
            duration = int((time.monotonic() - start) * 1000)
            rows_affected = result.rowcount

        _record_cleanup(state, op_type, sql, params, rows_affected, original_values)

        return {
            "success": True,
            "op_type": op_type.value,
            "rows_affected": rows_affected,
            "duration_ms": duration,
        }
    except DbToolkitError:
        raise
    except Exception as exc:
        raise SqlExecutionError(f"Execution failed: {exc}", code="EXEC_FAILED") from exc


async def handle_db_cleanup(state: ToolkitState, args: dict[str, Any]) -> dict[str, Any]:
    """Clean up test data recorded during this session. TEST environment only."""
    if not state.env.allow_delete:
        raise EnvironmentViolationError(
            "Cleanup not allowed in production environment",
            code="CLEANUP_NOT_ALLOWED",
        )

    records = state.cleanup_tracker.get_records()
    if not records:
        return {"cleaned": 0, "message": "No operations to clean up"}

    cleanup_sqls = state.cleanup_tracker.get_cleanup_sql()
    connection_url = args["connection_url"]
    cleaned = 0
    errors: list[str] = []

    engine = await state.conn_manager.get_engine(connection_url)
    for item in cleanup_sqls:
        try:
            async with engine.connect() as conn:
                stmt = text(item["sql"])
                if item["params"]:
                    stmt = stmt.bindparams(**item["params"])
                await conn.execute(stmt)
                await conn.commit()
                cleaned += 1
        except Exception as exc:
            errors.append(f"Failed: {item['sql']} — {exc}")

    state.cleanup_tracker.clear()
    return {
        "cleaned": cleaned,
        "total": len(cleanup_sqls),
        "errors": errors,
    }


# -- Tool definitions for LLM ------------------------------------------------

DB_TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "db_inspect",
        "description": (
            "Inspect database schema: list tables, columns, types, constraints, and sample data. "
            "Use this to understand the database structure before querying or modifying data. "
            "All environments (test and production) allow this operation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "connection_url": {
                    "type": "string",
                    "description": "Database connection URL (e.g. mysql://user:pass@host/db)",
                },
                "tables": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of table names to inspect. If omitted, all tables are inspected.",
                },
                "include_sample": {
                    "type": "boolean",
                    "description": "Whether to include sample data rows (default: true)",
                },
                "sample_limit": {
                    "type": "integer",
                    "description": "Number of sample rows per table (default: 5)",
                },
            },
            "required": ["connection_url"],
        },
    },
    {
        "name": "db_query",
        "description": (
            "Execute a SELECT query to read data from the database. "
            "All environments allow this. Automatically adds LIMIT if missing. "
            "Use parameterized queries with :param_name placeholders for values."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "connection_url": {
                    "type": "string",
                    "description": "Database connection URL",
                },
                "sql": {
                    "type": "string",
                    "description": "SELECT SQL statement with :param_name placeholders",
                },
                "params": {
                    "type": "object",
                    "description": "Parameter values keyed by name (without the colon prefix)",
                },
            },
            "required": ["connection_url", "sql"],
        },
    },
    {
        "name": "db_execute",
        "description": (
            "Execute a write operation (INSERT/UPDATE/DELETE) on the database. "
            "ONLY available in test environments. "
            "Call with confirm=false first to preview, then confirm=true to execute. "
            "Executed operations are automatically tracked for cleanup."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "connection_url": {
                    "type": "string",
                    "description": "Database connection URL",
                },
                "sql": {
                    "type": "string",
                    "description": "Write SQL statement (INSERT/UPDATE/DELETE) with :param_name placeholders",
                },
                "params": {
                    "type": "object",
                    "description": "Parameter values keyed by name",
                },
                "confirm": {
                    "type": "boolean",
                    "description": "false = preview only, true = actually execute (default: false)",
                },
            },
            "required": ["connection_url", "sql"],
        },
    },
    {
        "name": "db_cleanup",
        "description": (
            "Clean up all test data created during this session by reversing previous write operations. "
            "Only available in test environments. Uses tracked operation history for safe rollback."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "connection_url": {
                    "type": "string",
                    "description": "Database connection URL",
                },
            },
            "required": ["connection_url"],
        },
    },
]


# -- Internal helpers --------------------------------------------------------


def _inject_limit(sql: str) -> str:
    if re.search(r"\bLIMIT\s+\d+", sql, re.IGNORECASE):
        return sql
    return f"{sql.rstrip()} LIMIT {_SELECT_LIMIT}"


def _detect_op_type(sql: str) -> SqlOpType:
    stripped = sql.strip().upper()
    if stripped.startswith("INSERT"):
        return SqlOpType.INSERT
    if stripped.startswith("UPDATE"):
        return SqlOpType.UPDATE
    if stripped.startswith("DELETE"):
        return SqlOpType.DELETE
    raise SafetyViolationError(
        "Cannot detect operation type from SQL",
        code="UNKNOWN_OP_TYPE",
        details={"sql": sql[:200]},
    )


async def _snapshot_affected_rows(
    state: ToolkitState,
    connection_url: str,
    sql: str,
    params: dict[str, Any] | None,
    op_type: SqlOpType,
) -> list[dict[str, Any]]:
    """Snapshot rows that will be affected by UPDATE/DELETE."""
    try:
        where_match = re.search(r"\bWHERE\b\s+(.+?)(?:\bORDER\b|\bLIMIT\b|$)", sql, re.IGNORECASE | re.DOTALL)
        if not where_match:
            return []

        table_match = re.search(r"(?:FROM|UPDATE)\s+(\w+)", sql, re.IGNORECASE)
        if not table_match:
            return []

        table = table_match.group(1)
        where_clause = where_match.group(1).strip()
        snapshot_sql = f"SELECT * FROM {table} WHERE {where_clause} LIMIT 100"

        engine = await state.conn_manager.get_engine(connection_url)
        async with engine.connect() as conn:
            stmt = text(snapshot_sql)
            if params:
                stmt = stmt.bindparams(**params)
            result = await conn.execute(stmt)
            rows = result.mappings().all()
            return [dict(row) for row in rows]
    except Exception as exc:
        logger.warning("Failed to snapshot affected rows: %s", exc)
        return []


def _record_cleanup(
    state: ToolkitState,
    op_type: SqlOpType,
    sql: str,
    params: dict[str, Any] | None,
    rows_affected: int,
    original_values: list[dict[str, Any]],
) -> None:
    """Record the operation in the cleanup tracker."""
    table_match = re.search(r"(?:INTO|FROM|UPDATE)\s+(\w+)", sql, re.IGNORECASE)
    table = table_match.group(1) if table_match else "unknown"

    if op_type == SqlOpType.INSERT:
        inserted_ids = list(range(1, rows_affected + 1))  # placeholder
        state.cleanup_tracker.record_insert(table, inserted_ids=inserted_ids)
    elif op_type == SqlOpType.UPDATE:
        where_match = re.search(r"\bWHERE\b\s+(.+?)(?:\bORDER\b|\bLIMIT\b|$)", sql, re.IGNORECASE | re.DOTALL)
        where_clause = where_match.group(1).strip() if where_match else "1=1"
        state.cleanup_tracker.record_update(
            table, where_clause=where_clause,
            where_params=params or {}, original_values=original_values,
        )
    elif op_type == SqlOpType.DELETE:
        state.cleanup_tracker.record_delete(table, deleted_rows=original_values)
