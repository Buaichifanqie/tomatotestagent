from __future__ import annotations

import asyncio
import gc
import os
import socket
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from testagent.common.errors import DatabaseError
from testagent.db.migrate_sqlite_to_pg import SqliteToPgMigrator, build_sqlite_url
from testagent.db.migrations import async_downgrade, async_upgrade_head
from testagent.models.base import Base

PG_HOST = os.environ.get("TESTAGENT_PG_HOST", "localhost")
PG_PORT = int(os.environ.get("TESTAGENT_PG_PORT", "5432"))
PG_USER = os.environ.get("TESTAGENT_PG_USER", "testagent")
PG_PASSWORD = os.environ.get("TESTAGENT_PG_PASSWORD", "testagent")
PG_DB = os.environ.get("TESTAGENT_PG_DB", "testagent_test")

PG_URL = f"postgresql+asyncpg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"
SQLITE_TEST_DIR = Path(__file__).resolve().parent.parent / "_test_data"
SQLITE_TEST_DIR.mkdir(parents=True, exist_ok=True)


def _pg_is_available() -> bool:
    if os.environ.get("TESTAGENT_PG_HOST") is None:
        return False
    try:
        with socket.create_connection((PG_HOST, PG_PORT), timeout=2):
            return True
    except OSError:
        return False


requires_pg = pytest.mark.skipif(
    not _pg_is_available(),
    reason="PostgreSQL not available; set TESTAGENT_PG_HOST and ensure PG is running",
)


async def _safe_unlink(path: Path) -> None:
    for _ in range(5):
        gc.collect()
        await asyncio.sleep(0.1)
        try:
            if path.exists():
                path.unlink()
            return
        except PermissionError:
            continue
    if path.exists():
        path.unlink(missing_ok=True)


@pytest_asyncio.fixture
async def pg_connection() -> AsyncGenerator[AsyncConnection, None]:
    engine = create_async_engine(
        PG_URL,
        isolation_level="AUTOCOMMIT",
        connect_args={"timeout": 5, "command_timeout": 5},
    )
    try:
        async with engine.connect() as conn:
            yield conn
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def pg_schema_cleanup(pg_connection: AsyncConnection) -> AsyncGenerator[None, None]:
    tables = [
        "alembic_version",
        "defects",
        "test_results",
        "test_tasks",
        "test_plans",
        "test_sessions",
        "skill_definitions",
        "mcp_configs",
    ]
    for t in tables:
        await pg_connection.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
    await pg_connection.execute(text("DROP TABLE IF EXISTS _migration_checkpoint CASCADE"))
    await pg_connection.execute(text("DROP EXTENSION IF EXISTS pg_trgm"))
    yield
    for t in tables:
        await pg_connection.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
    await pg_connection.execute(text("DROP TABLE IF EXISTS _migration_checkpoint CASCADE"))


# ---------------------------------------------------------------------------
# 1. Alembic migration upgrade tests
# ---------------------------------------------------------------------------


class TestAlembicUpgrade:
    @requires_pg
    @pytest.mark.integration
    async def test_upgrade_head_on_postgresql(self, pg_schema_cleanup: None) -> None:
        await async_upgrade_head(database_url=PG_URL)
        engine = create_async_engine(PG_URL)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' ORDER BY table_name"
                    )
                )
                tables = {row[0] for row in result.fetchall()}
        finally:
            await engine.dispose()
        expected = {
            "test_sessions",
            "test_plans",
            "test_tasks",
            "test_results",
            "defects",
            "skill_definitions",
            "mcp_configs",
        }
        for t in expected:
            assert t in tables, f"Table {t} not found after upgrade"

    @requires_pg
    @pytest.mark.integration
    async def test_upgrade_downgrade_cycle(self, pg_schema_cleanup: None) -> None:
        await async_upgrade_head(database_url=PG_URL)
        await async_downgrade(revision="base", database_url=PG_URL)
        engine = create_async_engine(PG_URL)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name NOT LIKE '%alembic%'"
                    )
                )
                tables = {row[0] for row in result.fetchall()}
        finally:
            await engine.dispose()
        assert "test_sessions" not in tables
        assert "defects" not in tables

    @requires_pg
    @pytest.mark.integration
    async def test_upgrade_idempotent(self, pg_schema_cleanup: None) -> None:
        await async_upgrade_head(database_url=PG_URL)
        await async_upgrade_head(database_url=PG_URL)
        engine = create_async_engine(PG_URL)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
                )
                row = result.fetchone()
        finally:
            await engine.dispose()
        assert row is not None
        assert row[0] >= 7


# ---------------------------------------------------------------------------
# 2. GIN index tests
# ---------------------------------------------------------------------------


class TestGinIndexes:
    @requires_pg
    @pytest.mark.integration
    async def test_gin_index_on_defects_root_cause(self, pg_schema_cleanup: None) -> None:
        await async_upgrade_head(database_url=PG_URL)
        engine = create_async_engine(PG_URL)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes "
                        "WHERE tablename = 'defects' "
                        "AND indexname = 'ix_defects_root_cause_gin'"
                    )
                )
                row = result.fetchone()
        finally:
            await engine.dispose()
        assert row is not None, "GIN index ix_defects_root_cause_gin not found"
        assert "gin" in row[1].lower(), f"Index is not GIN: {row[1]}"

    @requires_pg
    @pytest.mark.integration
    async def test_gin_index_on_defects_description(self, pg_schema_cleanup: None) -> None:
        await async_upgrade_head(database_url=PG_URL)
        engine = create_async_engine(PG_URL)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes "
                        "WHERE tablename = 'defects' "
                        "AND indexname = 'ix_defects_description_gin'"
                    )
                )
                row = result.fetchone()
        finally:
            await engine.dispose()
        assert row is not None, "GIN trgm index ix_defects_description_gin not found"
        assert "gin_trgm_ops" in row[1], f"Missing gin_trgm_ops: {row[1]}"

    @requires_pg
    @pytest.mark.integration
    async def test_gin_index_on_test_results_assertion_results(
        self,
        pg_schema_cleanup: None,
    ) -> None:
        await async_upgrade_head(database_url=PG_URL)
        engine = create_async_engine(PG_URL)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(
                        "SELECT indexname, indexdef FROM pg_indexes "
                        "WHERE tablename = 'test_results' "
                        "AND indexname = 'ix_test_results_assertion_results_gin'"
                    )
                )
                row = result.fetchone()
        finally:
            await engine.dispose()
        assert row is not None, "GIN index ix_test_results_assertion_results_gin not found"
        assert "gin" in row[1].lower(), f"Index is not GIN: {row[1]}"


# ---------------------------------------------------------------------------
# 3. pg_trgm extension tests
# ---------------------------------------------------------------------------


class TestPgTrgmExtension:
    @requires_pg
    @pytest.mark.integration
    async def test_pg_trgm_extension_enabled(self, pg_schema_cleanup: None) -> None:
        await async_upgrade_head(database_url=PG_URL)
        engine = create_async_engine(PG_URL)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT extname FROM pg_extension WHERE extname = 'pg_trgm'"))
                row = result.fetchone()
        finally:
            await engine.dispose()
        assert row is not None, "pg_trgm extension not installed"
        assert row[0] == "pg_trgm"

    @requires_pg
    @pytest.mark.integration
    async def test_pg_trgm_functions_available(self, pg_schema_cleanup: None) -> None:
        await async_upgrade_head(database_url=PG_URL)
        engine = create_async_engine(PG_URL)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT similarity('hello', 'hallo')"))
                row = result.fetchone()
        finally:
            await engine.dispose()
        assert row is not None
        assert isinstance(row[0], float)


# ---------------------------------------------------------------------------
# 4. Data migration tool tests
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def sqlite_db_with_data() -> AsyncGenerator[str, None]:
    db_path = SQLITE_TEST_DIR / "test_migrate_source.db"
    await _safe_unlink(db_path)
    db_url = build_sqlite_url(str(db_path))

    engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with engine.connect() as conn:
        session_id = "session-001"
        await conn.execute(
            text(
                "INSERT INTO test_sessions (id, name, status, trigger_type, created_at) "
                "VALUES (:id, :name, :status, :trigger, :ts)"
            ),
            {
                "id": session_id,
                "name": "test-session",
                "status": "completed",
                "trigger": "manual",
                "ts": "2026-01-01T00:00:00",
            },
        )
        plan_id = "plan-001"
        await conn.execute(
            text(
                "INSERT INTO test_plans "
                "(id, session_id, strategy_type, plan_json, status, total_tasks, completed_tasks, created_at) "
                "VALUES (:id, :sid, :strategy, :json, :status, :total, :completed, :ts)"
            ),
            {
                "id": plan_id,
                "sid": session_id,
                "strategy": "smoke",
                "json": '{"steps":[]}',
                "status": "completed",
                "total": 1,
                "completed": 1,
                "ts": "2026-01-01T00:00:00",
            },
        )
        task_id = "task-001"
        await conn.execute(
            text(
                "INSERT INTO test_tasks "
                "(id, plan_id, task_type, task_config, status, isolation_level, "
                "priority, retry_count, created_at) "
                "VALUES (:id, :pid, :type, :config, :status, :iso, :prio, :retry, :ts)"
            ),
            {
                "id": task_id,
                "pid": plan_id,
                "type": "api_test",
                "config": '{"url":"/test"}',
                "status": "passed",
                "iso": "docker",
                "prio": 0,
                "retry": 0,
                "ts": "2026-01-01T00:00:00",
            },
        )
        result_id = "result-001"
        await conn.execute(
            text(
                "INSERT INTO test_results "
                "(id, task_id, status, duration_ms, assertion_results, created_at) "
                "VALUES (:id, :tid, :status, :dur, :assertions, :ts)"
            ),
            {
                "id": result_id,
                "tid": task_id,
                "status": "passed",
                "dur": 123.4,
                "assertions": '{"passed":1,"failed":0}',
                "ts": "2026-01-01T00:00:00",
            },
        )
        await conn.execute(
            text(
                "INSERT INTO defects "
                "(id, result_id, severity, category, title, description, root_cause, status, created_at) "
                "VALUES (:id, :rid, :sev, :cat, :title, :desc, :rc, :status, :ts)"
            ),
            {
                "id": "defect-001",
                "rid": result_id,
                "sev": "critical",
                "cat": "bug",
                "title": "Test defect",
                "desc": "A sample defect for migration testing",
                "rc": '{"type":"assertion"}',
                "status": "open",
                "ts": "2026-01-01T00:00:00",
            },
        )
        await conn.commit()

    await engine.dispose()
    yield str(db_path)
    await _safe_unlink(db_path)


@pytest_asyncio.fixture
async def postgres_target() -> AsyncGenerator[None, None]:
    # Clean alembic_version first to ensure fresh upgrade
    engine_clean = create_async_engine(PG_URL, isolation_level="AUTOCOMMIT")
    try:
        async with engine_clean.connect() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))
            await conn.execute(text("DROP TABLE IF EXISTS _migration_checkpoint CASCADE"))
    finally:
        await engine_clean.dispose()
    await async_upgrade_head(database_url=PG_URL)
    yield
    engine = create_async_engine(PG_URL, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS _migration_checkpoint CASCADE"))
            for t in [
                "alembic_version",
                "defects",
                "test_results",
                "test_tasks",
                "test_plans",
                "mcp_configs",
                "skill_definitions",
                "test_sessions",
            ]:
                await conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
            await conn.execute(text("DROP EXTENSION IF EXISTS pg_trgm"))
    finally:
        await engine.dispose()


class TestDataMigrationTool:
    @requires_pg
    @pytest.mark.integration
    async def test_migrate_all_tables(
        self,
        sqlite_db_with_data: str,
        postgres_target: None,
    ) -> None:
        async with SqliteToPgMigrator(
            sqlite_url=build_sqlite_url(sqlite_db_with_data),
            postgresql_url=PG_URL,
            batch_size=100,
        ) as migrator:
            stats_list = await migrator.run()

        stats_by_name = {s.table_name: s for s in stats_list}
        assert stats_by_name["test_sessions"].completed
        assert stats_by_name["test_plans"].completed
        assert stats_by_name["test_tasks"].completed
        assert stats_by_name["test_results"].completed
        assert stats_by_name["defects"].completed

        engine = create_async_engine(PG_URL)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT id, name FROM test_sessions WHERE id = 'session-001'"))
                row = result.fetchone()
        finally:
            await engine.dispose()
        assert row is not None
        assert row[1] == "test-session"

    @requires_pg
    @pytest.mark.integration
    async def test_validation_only(
        self,
        sqlite_db_with_data: str,
        postgres_target: None,
    ) -> None:
        async with SqliteToPgMigrator(
            sqlite_url=build_sqlite_url(sqlite_db_with_data),
            postgresql_url=PG_URL,
            batch_size=100,
        ) as migrator:
            with pytest.raises(DatabaseError, match="validation failed"):
                await migrator.validate()

    @requires_pg
    @pytest.mark.integration
    async def test_migrate_then_validate(
        self,
        sqlite_db_with_data: str,
        postgres_target: None,
    ) -> None:
        async with SqliteToPgMigrator(
            sqlite_url=build_sqlite_url(sqlite_db_with_data),
            postgresql_url=PG_URL,
            batch_size=100,
        ) as migrator:
            await migrator.run()
            stats_list = await migrator.validate()

        assert all(s.completed for s in stats_list)
        for s in stats_list:
            msg = f"Table {s.table_name}: src={s.source_count} != tgt={s.target_count}"
            assert s.source_count == s.target_count, msg

    @requires_pg
    @pytest.mark.integration
    async def test_checkpoint_resume(
        self,
        sqlite_db_with_data: str,
        postgres_target: None,
    ) -> None:
        async with SqliteToPgMigrator(
            sqlite_url=build_sqlite_url(sqlite_db_with_data),
            postgresql_url=PG_URL,
            batch_size=100,
        ) as migrator:
            first_stats = await migrator.run()

        async with SqliteToPgMigrator(
            sqlite_url=build_sqlite_url(sqlite_db_with_data),
            postgresql_url=PG_URL,
            batch_size=100,
        ) as migrator:
            second_stats = await migrator.run()

        assert len(first_stats) == len(second_stats)
        for s in second_stats:
            assert s.completed, f"Table {s.table_name} not completed on resume"

    @requires_pg
    @pytest.mark.integration
    async def test_migrate_empty_tables(self, postgres_target: None) -> None:
        empty_db_path = SQLITE_TEST_DIR / "test_migrate_empty.db"
        await _safe_unlink(empty_db_path)
        db_url = build_sqlite_url(str(empty_db_path))
        engine = create_async_engine(db_url, connect_args={"check_same_thread": False})
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

        try:
            async with SqliteToPgMigrator(
                sqlite_url=db_url,
                postgresql_url=PG_URL,
                batch_size=100,
            ) as migrator:
                stats_list = await migrator.run()

            assert all(s.completed for s in stats_list)
            for s in stats_list:
                assert s.source_count == 0
                assert s.target_count == 0
        finally:
            await _safe_unlink(empty_db_path)
