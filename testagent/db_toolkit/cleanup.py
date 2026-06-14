from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from testagent.db_toolkit.models import SqlOpType


@dataclass
class CleanupRecord:
    op_type: SqlOpType
    table: str
    inserted_ids: list[Any] = field(default_factory=list)
    where_clause: str = ""
    where_params: dict[str, Any] = field(default_factory=dict)
    original_values: list[dict[str, Any]] = field(default_factory=list)
    deleted_rows: list[dict[str, Any]] = field(default_factory=list)


class CleanupTracker:
    """Tracks write operations during a session for automatic cleanup."""

    def __init__(self) -> None:
        self._records: list[CleanupRecord] = []

    def record_insert(self, table: str, inserted_ids: list[Any]) -> None:
        self._records.append(CleanupRecord(
            op_type=SqlOpType.INSERT,
            table=table,
            inserted_ids=inserted_ids,
        ))

    def record_update(
        self,
        table: str,
        where_clause: str,
        where_params: dict[str, Any],
        original_values: list[dict[str, Any]],
    ) -> None:
        self._records.append(CleanupRecord(
            op_type=SqlOpType.UPDATE,
            table=table,
            where_clause=where_clause,
            where_params=where_params,
            original_values=original_values,
        ))

    def record_delete(self, table: str, deleted_rows: list[dict[str, Any]]) -> None:
        self._records.append(CleanupRecord(
            op_type=SqlOpType.DELETE,
            table=table,
            deleted_rows=deleted_rows,
        ))

    def get_records(self) -> list[CleanupRecord]:
        return list(self._records)

    def get_cleanup_sql(self) -> list[dict[str, Any]]:
        """Generate cleanup SQL in reverse order of operations."""
        sqls: list[dict[str, Any]] = []
        for record in reversed(self._records):
            if record.op_type == SqlOpType.INSERT:
                sqls.append(self._cleanup_insert(record))
            elif record.op_type == SqlOpType.UPDATE:
                sqls.append(self._cleanup_update(record))
            elif record.op_type == SqlOpType.DELETE:
                sqls.append(self._cleanup_delete(record))
        return sqls

    def clear(self) -> None:
        self._records.clear()

    @staticmethod
    def _cleanup_insert(record: CleanupRecord) -> dict[str, Any]:
        placeholders = ", ".join(f":id_{i}" for i in range(len(record.inserted_ids)))
        params = {f"id_{i}": id_ for i, id_ in enumerate(record.inserted_ids)}
        return {
            "sql": f"DELETE FROM {record.table} WHERE id IN ({placeholders})",
            "params": params,
            "description": f"Cleanup: delete {len(record.inserted_ids)} inserted rows from {record.table}",
        }

    @staticmethod
    def _cleanup_update(record: CleanupRecord) -> dict[str, Any]:
        if not record.original_values:
            return {
                "sql": f"-- Cannot restore {record.table}: original values not captured",
                "params": {},
                "description": f"Warning: original values for {record.table} UPDATE not available",
            }
        row = record.original_values[0]
        set_parts = []
        for col, val in row.items():
            set_parts.append(f"{col} = '{val}'")
        return {
            "sql": f"UPDATE {record.table} SET {', '.join(set_parts)} WHERE {record.where_clause}",
            "params": record.where_params,
            "description": f"Cleanup: restore original values in {record.table}",
        }

    @staticmethod
    def _cleanup_delete(record: CleanupRecord) -> dict[str, Any]:
        if not record.deleted_rows:
            return {
                "sql": f"-- Cannot restore {record.table}: deleted rows not captured",
                "params": {},
                "description": f"Warning: deleted rows for {record.table} not available",
            }
        row = record.deleted_rows[0]
        cols = list(row.keys())
        col_list = ", ".join(cols)
        placeholders = ", ".join(f":{col}" for col in cols)
        return {
            "sql": f"INSERT INTO {record.table} ({col_list}) VALUES ({placeholders})",
            "params": dict(row),
            "description": f"Cleanup: re-insert deleted row into {record.table}",
        }
