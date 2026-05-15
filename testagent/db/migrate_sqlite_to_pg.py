from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from testagent.common.errors import DatabaseError
from testagent.common.logging import get_logger

logger = get_logger(__name__)

_CHECKPOINT_TABLE = "_migration_checkpoint"
_DEFAULT_BATCH_SIZE = 500

_TABLE_ORDER: list[str] = [
    "test_sessions",
    "skill_definitions",
    "mcp_configs",
    "test_plans",
    "test_tasks",
    "test_results",
    "defects",
]

_TABLE_DEPENDENCIES: dict[str, list[str]] = {
    "test_sessions": [],
    "skill_definitions": [],
    "mcp_configs": ["test_sessions"],
    "test_plans": ["test_sessions"],
    "test_tasks": ["test_plans"],
    "test_results": ["test_tasks"],
    "defects": ["test_results"],
}


@dataclass
class MigrationStats:
    table_name: str
    source_count: int = 0
    target_count: int = 0
    batches: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    completed: bool = False


class SqliteToPgMigrator:
    """Data migration tool from SQLite to PostgreSQL.

    Features:
      - Asynchronous batch read/write with configurable batch size
      - Checkpoint-based resume support via _migration_checkpoint table
      - Data validation after migration (compare source/target row counts)
      - Table-level dependency ordering for FK constraint compliance
      - Comprehensive structured logging and error reporting
    """

    def __init__(
        self,
        sqlite_url: str,
        postgresql_url: str,
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ) -> None:
        self._sqlite_url = sqlite_url
        self._postgresql_url = postgresql_url
        self._batch_size = batch_size
        self._sqlite_engine: AsyncEngine | None = None
        self._postgres_engine: AsyncEngine | None = None
        self._stats: dict[str, MigrationStats] = {}

    async def __aenter__(self) -> SqliteToPgMigrator:
        self._sqlite_engine = create_async_engine(
            self._sqlite_url,
            connect_args={"check_same_thread": False},
            pool_pre_ping=True,
        )
        self._postgres_engine = create_async_engine(
            self._postgresql_url,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._sqlite_engine is not None:
            await self._sqlite_engine.dispose()
        if self._postgres_engine is not None:
            await self._postgres_engine.dispose()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self) -> list[MigrationStats]:
        """Execute the full migration: schema setup, data copy, validation."""
        logger.info("Starting SQLite → PostgreSQL migration")

        self._stats = {name: MigrationStats(table_name=name) for name in _TABLE_ORDER}

        await self._ensure_postgres_schema()
        checkpoint = await self._load_checkpoint()

        for table_name in _TABLE_ORDER:
            if checkpoint.get(table_name, {}).get("completed", False):
                logger.info("Skipping already-migrated table: %s", table_name)
                self._stats[table_name].completed = True
                continue

            depends = _TABLE_DEPENDENCIES.get(table_name, [])
            missing = [d for d in depends if not checkpoint.get(d, {}).get("completed", False)]
            if missing:
                raise DatabaseError(
                    f"Cannot migrate {table_name}: dependencies not yet migrated: {missing}",
                    code="DB_MIGRATION_DEP_MISSING",
                    details={"table": table_name, "dependencies": missing},
                )

            await self._migrate_table(table_name)
            checkpoint[table_name] = {"completed": True, "migrated_at": datetime.now(UTC)}
            await self._save_checkpoint(checkpoint)

        await self._validate_all()
        logger.info("Migration completed successfully")
        return list(self._stats.values())

    async def validate(self) -> list[MigrationStats]:
        """Run validation only (no data copy)."""
        self._stats = {name: MigrationStats(table_name=name) for name in _TABLE_ORDER}
        await self._validate_all()
        return list(self._stats.values())

    # ------------------------------------------------------------------
    # Schema setup
    # ------------------------------------------------------------------

    async def _ensure_postgres_schema(self) -> None:
        """Ensure the _migration_checkpoint table exists in PostgreSQL."""
        assert self._postgres_engine is not None
        async with self._postgres_engine.connect() as conn:
            await conn.execute(
                text(
                    f"""
                    CREATE TABLE IF NOT EXISTS {_CHECKPOINT_TABLE} (
                        table_name VARCHAR(255) PRIMARY KEY,
                        migrated_rows INTEGER NOT NULL DEFAULT 0,
                        completed BOOLEAN NOT NULL DEFAULT FALSE,
                        migrated_at TIMESTAMPTZ
                    )
                    """
                )
            )
            await conn.commit()

    # ------------------------------------------------------------------
    # Checkpoint persistence
    # ------------------------------------------------------------------

    async def _load_checkpoint(self) -> dict[str, Any]:
        """Load checkpoint from PostgreSQL _migration_checkpoint table."""
        assert self._postgres_engine is not None
        checkpoint: dict[str, Any] = {}
        async with self._postgres_engine.connect() as conn:
            result = await conn.execute(text(f"SELECT table_name, completed, migrated_at FROM {_CHECKPOINT_TABLE}"))
            for row in result.fetchall():
                checkpoint[row[0]] = {
                    "completed": row[1],
                    "migrated_at": row[2],
                }
        return checkpoint

    async def _save_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        """Persist checkpoint state to PostgreSQL."""
        assert self._postgres_engine is not None
        async with self._postgres_engine.connect() as conn:
            for table_name, state in checkpoint.items():
                await conn.execute(
                    text(
                        f"""
                        INSERT INTO {_CHECKPOINT_TABLE} (table_name, completed, migrated_at)
                        VALUES (:name, :completed, :migrated_at)
                        ON CONFLICT (table_name)
                        DO UPDATE SET completed = :completed, migrated_at = :migrated_at
                        """
                    ),
                    {
                        "name": table_name,
                        "completed": state.get("completed", False),
                        "migrated_at": state.get("migrated_at"),
                    },
                )
            await conn.commit()

    # ------------------------------------------------------------------
    # Table migration
    # ------------------------------------------------------------------

    async def _migrate_table(self, table_name: str) -> None:
        """Migrate a single table from SQLite to PostgreSQL in batches."""
        assert self._sqlite_engine is not None
        assert self._postgres_engine is not None

        stats = self._stats[table_name]
        logger.info("Migrating table: %s", table_name)
        start = datetime.now(UTC)

        try:
            source_meta = await self._reflect_table(self._sqlite_engine, table_name)
            target_meta = await self._reflect_table(self._postgres_engine, table_name)

            source_table = source_meta.tables[table_name]
            target_table = target_meta.tables[table_name]

            source_columns = [c.name for c in source_table.columns]
            target_columns = [c.name for c in target_table.columns]
            shared_columns = [c for c in source_columns if c in target_columns]

            async with self._sqlite_engine.connect() as src_conn:
                total = await self._count_rows(src_conn, table_name)
                stats.source_count = total
                logger.info("Table %s: source has %d rows", table_name, total)

                offset = 0
                while offset < total:
                    rows = await self._read_batch(src_conn, source_table, shared_columns, offset)
                    if not rows:
                        break
                    await self._write_batch(target_table, shared_columns, rows)
                    stats.batches += 1
                    offset += len(rows)
                    logger.info(
                        "Table %s: migrated %d/%d rows (batch %d)",
                        table_name,
                        offset,
                        total,
                        stats.batches,
                    )

            async with self._postgres_engine.connect() as tgt_conn:
                stats.target_count = await self._count_rows(tgt_conn, table_name)

            stats.duration_ms = (datetime.now(UTC) - start).total_seconds() * 1000
            stats.completed = True
            logger.info(
                "Table %s: migrated %d rows in %d batches (%.0f ms)",
                table_name,
                stats.target_count,
                stats.batches,
                stats.duration_ms,
            )
        except Exception as exc:
            stats.errors.append(str(exc))
            logger.error("Table %s migration failed: %s", table_name, str(exc))
            raise

    async def _reflect_table(self, engine: AsyncEngine, table_name: str) -> MetaData:
        """Reflect a table definition from the database."""
        meta = MetaData()
        async with engine.connect() as conn:
            await conn.run_sync(lambda sync_conn: meta.reflect(bind=sync_conn, only=[table_name]))
        if table_name not in meta.tables:
            raise DatabaseError(
                f"Table {table_name} not found in database",
                code="DB_MIGRATION_TABLE_NOT_FOUND",
                details={"table": table_name},
            )
        return meta

    async def _count_rows(self, conn: AsyncConnection, table_name: str) -> int:
        """Count rows in a table."""
        result = await conn.execute(text(f"SELECT COUNT(*) FROM {table_name}"))
        row = result.fetchone()
        return row[0] if row else 0

    async def _read_batch(
        self,
        conn: AsyncConnection,
        table: Table,
        columns: list[str],
        offset: int,
    ) -> list[dict[str, Any]]:
        """Read a batch of rows from the source table."""
        stmt = select(*(table.c[c] for c in columns)).limit(self._batch_size).offset(offset)
        result = await conn.execute(stmt)
        rows = result.fetchall()
        return [dict(row._mapping) for row in rows]

    async def _write_batch(
        self,
        table: Table,
        columns: list[str],
        rows: list[dict[str, Any]],
    ) -> None:
        """Write a batch of rows to the target table."""
        assert self._postgres_engine is not None
        if not rows:
            return

        processed: list[dict[str, Any]] = []
        for row in rows:
            prepared: dict[str, Any] = {}
            for col in columns:
                val = row.get(col)
                if isinstance(val, (dict, list)):
                    val = json.dumps(val, ensure_ascii=False)
                prepared[col] = val
            processed.append(prepared)

        async with self._postgres_engine.connect() as conn:
            await conn.execute(table.insert().values(processed))
            await conn.commit()

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    async def _validate_all(self) -> None:
        """Compare row counts between source and target for all tables."""
        assert self._sqlite_engine is not None
        assert self._postgres_engine is not None

        logger.info("Starting data validation across all tables")
        errors: list[str] = []

        async with (
            self._sqlite_engine.connect() as src_conn,
            self._postgres_engine.connect() as tgt_conn,
        ):
            for table_name in _TABLE_ORDER:
                src_count = await self._count_rows(src_conn, table_name)
                tgt_count = await self._count_rows(tgt_conn, table_name)
                self._stats[table_name].source_count = src_count
                self._stats[table_name].target_count = tgt_count
                if src_count != tgt_count:
                    msg = f"Table {table_name}: row count mismatch (source={src_count}, target={tgt_count})"
                    errors.append(msg)
                    self._stats[table_name].errors.append(msg)
                    logger.error(msg)
                else:
                    logger.info("Table %s: validated OK (%d rows)", table_name, src_count)

        if errors:
            raise DatabaseError(
                "Data validation failed",
                code="DB_MIGRATION_VALIDATION_FAILED",
                details={"errors": errors},
            )
        for table_name in _TABLE_ORDER:
            self._stats[table_name].completed = True
        logger.info("All tables validated successfully")


def build_sqlite_url(db_path: str | Path) -> str:
    """Build a SQLAlchemy async URL for a SQLite file path."""
    path = Path(db_path).resolve()
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def build_postgresql_url(
    user: str,
    password: str,
    host: str = "localhost",
    port: int = 5432,
    database: str = "testagent",
) -> str:
    """Build a SQLAlchemy async URL for PostgreSQL."""
    return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{database}"


# ------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------


async def run_migration(
    sqlite_path: str,
    pg_user: str = "testagent",
    pg_password: str = "",
    pg_host: str = "localhost",
    pg_port: int = 5432,
    pg_db: str = "testagent",
    batch_size: int = _DEFAULT_BATCH_SIZE,
    validate_only: bool = False,
) -> list[MigrationStats]:
    """Run the full SQLite → PostgreSQL migration.

    Args:
        sqlite_path: Path to the SQLite database file.
        pg_user: PostgreSQL user.
        pg_password: PostgreSQL password.
        pg_host: PostgreSQL host.
        pg_port: PostgreSQL port.
        pg_db: PostgreSQL database name.
        batch_size: Rows per batch for bulk insert.
        validate_only: If True, only validate row counts without copying data.

    Returns:
        List of MigrationStats for each migrated table.
    """
    sqlite_url = build_sqlite_url(sqlite_path)
    pg_url = build_postgresql_url(pg_user, pg_password, pg_host, pg_port, pg_db)

    async with SqliteToPgMigrator(
        sqlite_url=sqlite_url,
        postgresql_url=pg_url,
        batch_size=batch_size,
    ) as migrator:
        if validate_only:
            return await migrator.validate()
        return await migrator.run()
