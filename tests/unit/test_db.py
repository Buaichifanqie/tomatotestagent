from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from testagent.common.errors import DatabaseError
from testagent.db.engine import (
    close_db,
    get_engine,
    get_session,
    get_session_factory,
    init_db,
    reset_engine,
)
from testagent.db.migrations import downgrade, upgrade_head
from testagent.db.repository import (
    DefectRepository,
    Repository,
    SessionRepository,
    TaskRepository,
)
from testagent.models.base import Base
from testagent.models.defect import Defect
from testagent.models.plan import TestPlan, TestTask
from testagent.models.result import TestResult
from testagent.models.session import TestSession

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("TESTAGENT_DATABASE_URL", "sqlite+aiosqlite://")
    from testagent.config.settings import reset_settings

    reset_settings()
    reset_engine()
    yield
    reset_settings()
    reset_engine()


@pytest_asyncio.fixture()
async def async_engine() -> AsyncGenerator[AsyncEngine, None]:
    eng = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await eng.dispose()


@pytest_asyncio.fixture()
async def async_session(async_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(bind=async_engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session


class TestEngineCreation:
    async def test_get_engine_returns_async_engine(self) -> None:
        engine = get_engine()
        assert engine is not None
        assert "sqlite" in str(engine.url)
        await engine.dispose()
        reset_engine()

    async def test_get_engine_singleton(self) -> None:
        e1 = get_engine()
        e2 = get_engine()
        assert e1 is e2
        await e1.dispose()
        reset_engine()

    async def test_get_session_factory(self) -> None:
        factory = get_session_factory()
        assert factory is not None
        await get_engine().dispose()
        reset_engine()

    async def test_init_db_creates_tables(self) -> None:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await init_db()
        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            tables = [row[0] for row in result.fetchall()]
        assert "test_sessions" in tables
        assert "test_plans" in tables
        assert "test_tasks" in tables
        assert "test_results" in tables
        assert "defects" in tables
        assert "skill_definitions" in tables
        assert "mcp_configs" in tables
        await close_db()

    async def test_close_db_disposes_engine(self) -> None:
        engine = get_engine()
        assert engine is not None
        await close_db()
        from testagent.db import engine as engine_mod

        assert engine_mod._engine is None

    async def test_reset_engine(self) -> None:
        get_engine()
        reset_engine()
        from testagent.db import engine as engine_mod

        assert engine_mod._engine is None
        assert engine_mod._session_factory is None


class TestWALMode:
    async def test_wal_mode_activated(self, async_engine: AsyncEngine) -> None:
        async with async_engine.connect() as conn:
            result = await conn.execute(text("PRAGMA journal_mode"))
            row = result.fetchone()
            assert row is not None
            journal_mode = row[0].upper()
            assert journal_mode in ("WAL", "MEMORY")

    async def test_foreign_keys_enabled(self, async_engine: AsyncEngine) -> None:
        async with async_engine.connect() as conn:
            await conn.execute(text("PRAGMA foreign_keys=ON"))
            result = await conn.execute(text("PRAGMA foreign_keys"))
            row = result.fetchone()
            assert row is not None
            assert row[0] == 1

    async def test_json1_available(self, async_engine: AsyncEngine) -> None:
        async with async_engine.connect() as conn:
            result = await conn.execute(text("SELECT json_array(1, 2, 3)"))
            row = result.fetchone()
            assert row is not None
            assert row[0] == "[1,2,3]"


class TestGetSessionContextManager:
    async def test_get_session_commits_on_success(self) -> None:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with get_session() as session:
            s = TestSession(name="ctx-test", status="pending", trigger_type="manual")
            session.add(s)
        async with get_session() as session:
            from sqlalchemy import select

            stmt = select(TestSession).where(TestSession.name == "ctx-test")
            result = await session.execute(stmt)
            found = result.scalar_one_or_none()
            assert found is not None
            assert found.name == "ctx-test"
        await close_db()

    async def test_get_session_rollback_on_error(self) -> None:
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        with pytest.raises(ValueError):
            async with get_session() as session:
                s = TestSession(name="rollback-test", status="pending", trigger_type="manual")
                session.add(s)
                raise ValueError("test error")
        async with get_session() as session:
            from sqlalchemy import select

            stmt = select(TestSession).where(TestSession.name == "rollback-test")
            result = await session.execute(stmt)
            found = result.scalar_one_or_none()
            assert found is None
        await close_db()


class TestRepositoryCRUD:
    async def test_create_entity(self, async_session: AsyncSession) -> None:
        repo: Repository[TestSession] = Repository[TestSession](async_session)
        repo._model_class = TestSession
        session = TestSession(name="create-test", status="pending", trigger_type="manual")
        result = await repo.create(session)
        assert result.id is not None
        assert result.name == "create-test"

    async def test_get_by_id(self, async_session: AsyncSession) -> None:
        repo: Repository[TestSession] = Repository[TestSession](async_session)
        repo._model_class = TestSession
        session = TestSession(name="get-test", status="pending", trigger_type="manual")
        created = await repo.create(session)
        found = await repo.get_by_id(created.id)
        assert found is not None
        assert found.name == "get-test"

    async def test_get_by_id_not_found(self, async_session: AsyncSession) -> None:
        repo: Repository[TestSession] = Repository[TestSession](async_session)
        repo._model_class = TestSession
        found = await repo.get_by_id("nonexistent-id")
        assert found is None

    async def test_get_all(self, async_session: AsyncSession) -> None:
        repo: Repository[TestSession] = Repository[TestSession](async_session)
        repo._model_class = TestSession
        for i in range(5):
            s = TestSession(name=f"all-test-{i}", status="pending", trigger_type="manual")
            await repo.create(s)
        results = await repo.get_all()
        assert len(results) == 5

    async def test_get_all_with_pagination(self, async_session: AsyncSession) -> None:
        repo: Repository[TestSession] = Repository[TestSession](async_session)
        repo._model_class = TestSession
        for i in range(10):
            s = TestSession(name=f"page-test-{i}", status="pending", trigger_type="manual")
            await repo.create(s)
        page1 = await repo.get_all(offset=0, limit=5)
        page2 = await repo.get_all(offset=5, limit=5)
        assert len(page1) == 5
        assert len(page2) == 5

    async def test_update_entity(self, async_session: AsyncSession) -> None:
        repo: Repository[TestSession] = Repository[TestSession](async_session)
        repo._model_class = TestSession
        session = TestSession(name="update-test", status="pending", trigger_type="manual")
        created = await repo.create(session)
        updated = await repo.update(created.id, {"status": "planning"})
        assert updated is not None
        assert updated.status == "planning"

    async def test_update_nonexistent(self, async_session: AsyncSession) -> None:
        repo: Repository[TestSession] = Repository[TestSession](async_session)
        repo._model_class = TestSession
        updated = await repo.update("nonexistent-id", {"status": "planning"})
        assert updated is None

    async def test_delete_entity(self, async_session: AsyncSession) -> None:
        repo: Repository[TestSession] = Repository[TestSession](async_session)
        repo._model_class = TestSession
        session = TestSession(name="delete-test", status="pending", trigger_type="manual")
        created = await repo.create(session)
        deleted = await repo.delete(created.id)
        assert deleted is True
        found = await repo.get_by_id(created.id)
        assert found is None

    async def test_delete_nonexistent(self, async_session: AsyncSession) -> None:
        repo: Repository[TestSession] = Repository[TestSession](async_session)
        repo._model_class = TestSession
        deleted = await repo.delete("nonexistent-id")
        assert deleted is False


class TestSessionRepository:
    async def test_get_by_status(self, async_session: AsyncSession) -> None:
        repo = SessionRepository(async_session)
        s1 = TestSession(name="pending-1", status="pending", trigger_type="manual")
        s2 = TestSession(name="executing-1", status="executing", trigger_type="ci_push")
        s3 = TestSession(name="pending-2", status="pending", trigger_type="manual")
        await repo.create(s1)
        await repo.create(s2)
        await repo.create(s3)
        pending = await repo.get_by_status("pending")
        assert len(pending) == 2
        assert all(s.status == "pending" for s in pending)

    async def test_get_by_status_empty(self, async_session: AsyncSession) -> None:
        repo = SessionRepository(async_session)
        results = await repo.get_by_status("completed")
        assert len(results) == 0


class TestTaskRepository:
    async def _create_plan_with_tasks(self, session: AsyncSession) -> tuple[str, str, str]:
        s = TestSession(name="task-repo-test", status="executing", trigger_type="manual")
        session.add(s)
        await session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": []},
        )
        session.add(p)
        await session.flush()
        t1 = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/t1"},
            priority=10,
        )
        t2 = TestTask(
            plan_id=p.id,
            task_type="web_test",
            task_config={"url": "/t2"},
            priority=5,
        )
        session.add(t1)
        session.add(t2)
        await session.flush()
        return p.id, t1.id, t2.id

    async def test_get_by_plan_id(self, async_session: AsyncSession) -> None:
        repo = TaskRepository(async_session)
        plan_id, t1_id, t2_id = await self._create_plan_with_tasks(async_session)
        tasks = await repo.get_by_plan_id(plan_id)
        assert len(tasks) == 2
        task_ids = {t.id for t in tasks}
        assert t1_id in task_ids
        assert t2_id in task_ids

    async def test_get_by_plan_id_ordered_by_priority(self, async_session: AsyncSession) -> None:
        repo = TaskRepository(async_session)
        plan_id, t1_id, t2_id = await self._create_plan_with_tasks(async_session)
        tasks = await repo.get_by_plan_id(plan_id)
        assert tasks[0].id == t1_id
        assert tasks[0].priority == 10
        assert tasks[1].id == t2_id
        assert tasks[1].priority == 5

    async def test_get_dependent_tasks(self, async_session: AsyncSession) -> None:
        s = TestSession(name="dep-repo-test", status="executing", trigger_type="manual")
        async_session.add(s)
        await async_session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="regression",
            plan_json={"steps": []},
        )
        async_session.add(p)
        await async_session.flush()
        t1 = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/setup"},
        )
        async_session.add(t1)
        await async_session.flush()
        t2 = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/verify"},
            depends_on=t1.id,
        )
        async_session.add(t2)
        await async_session.flush()
        repo = TaskRepository(async_session)
        dependent = await repo.get_dependent_tasks(t1.id)
        assert len(dependent) == 1
        assert dependent[0].id == t2.id

    async def test_get_dependent_tasks_empty(self, async_session: AsyncSession) -> None:
        s = TestSession(name="dep-empty-test", status="executing", trigger_type="manual")
        async_session.add(s)
        await async_session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": []},
        )
        async_session.add(p)
        await async_session.flush()
        t1 = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/standalone"},
        )
        async_session.add(t1)
        await async_session.flush()
        repo = TaskRepository(async_session)
        dependent = await repo.get_dependent_tasks(t1.id)
        assert len(dependent) == 0


class TestDefectRepository:
    async def _create_defects(self, session: AsyncSession) -> None:
        s = TestSession(name="defect-repo-test", status="analyzing", trigger_type="manual")
        session.add(s)
        await session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": []},
        )
        session.add(p)
        await session.flush()
        t1 = TestTask(plan_id=p.id, task_type="api_test", task_config={"url": "/t1"})
        t2 = TestTask(plan_id=p.id, task_type="api_test", task_config={"url": "/t2"})
        t3 = TestTask(plan_id=p.id, task_type="api_test", task_config={"url": "/t3"})
        session.add(t1)
        session.add(t2)
        session.add(t3)
        await session.flush()
        r1 = TestResult(task_id=t1.id, status="failed")
        r2 = TestResult(task_id=t2.id, status="failed")
        r3 = TestResult(task_id=t3.id, status="failed")
        session.add(r1)
        session.add(r2)
        session.add(r3)
        await session.flush()
        d1 = Defect(result_id=r1.id, severity="critical", category="bug", title="Bug 1")
        d2 = Defect(result_id=r2.id, severity="major", category="flaky", title="Flaky 1")
        d3 = Defect(result_id=r3.id, severity="critical", category="environment", title="Env 1")
        session.add(d1)
        session.add(d2)
        session.add(d3)
        await session.flush()

    async def test_get_by_severity(self, async_session: AsyncSession) -> None:
        await self._create_defects(async_session)
        repo = DefectRepository(async_session)
        critical = await repo.get_by_severity("critical")
        assert len(critical) == 2
        assert all(d.severity == "critical" for d in critical)

    async def test_get_by_category(self, async_session: AsyncSession) -> None:
        await self._create_defects(async_session)
        repo = DefectRepository(async_session)
        flaky = await repo.get_by_category("flaky")
        assert len(flaky) == 1
        assert flaky[0].category == "flaky"

    async def test_get_by_severity_empty(self, async_session: AsyncSession) -> None:
        repo = DefectRepository(async_session)
        results = await repo.get_by_severity("trivial")
        assert len(results) == 0

    async def test_get_by_category_empty(self, async_session: AsyncSession) -> None:
        repo = DefectRepository(async_session)
        results = await repo.get_by_category("configuration")
        assert len(results) == 0


class TestMigration:
    def test_upgrade_head_creates_tables(self, tmp_path: object) -> None:
        db_path = os.path.join(str(tmp_path), "test_migrate.db")
        db_url = f"sqlite+aiosqlite:///{db_path}"
        upgrade_head(database_url=db_url)
        import sqlite3

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        assert "test_sessions" in tables
        assert "test_plans" in tables
        assert "test_tasks" in tables
        assert "test_results" in tables
        assert "defects" in tables
        assert "skill_definitions" in tables
        assert "mcp_configs" in tables

    def test_downgrade_drops_tables(self, tmp_path: object) -> None:
        db_path = os.path.join(str(tmp_path), "test_downgrade.db")
        db_url = f"sqlite+aiosqlite:///{db_path}"
        upgrade_head(database_url=db_url)
        downgrade(revision="base", database_url=db_url)
        import sqlite3

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        assert "test_sessions" not in tables
        assert "defects" not in tables

    def test_migration_idempotent(self, tmp_path: object) -> None:
        db_path = os.path.join(str(tmp_path), "test_idempotent.db")
        db_url = f"sqlite+aiosqlite:///{db_path}"
        upgrade_head(database_url=db_url)
        upgrade_head(database_url=db_url)
        import sqlite3

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()
        assert "test_sessions" in tables


class TestRepositoryErrorHandling:
    async def test_create_duplicate_raises_database_error(self, async_session: AsyncSession) -> None:
        repo: Repository[TestSession] = Repository[TestSession](async_session)
        repo._model_class = TestSession
        s = TestSession(id="dup-id", name="dup-test", status="pending", trigger_type="manual")
        await repo.create(s)
        async_session.expunge(s)
        with pytest.raises(DatabaseError):
            dup = TestSession(id="dup-id", name="dup-test-2", status="pending", trigger_type="manual")
            await repo.create(dup)
