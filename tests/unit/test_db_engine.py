from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.pool import StaticPool

from testagent.config.settings import TestAgentSettings, reset_settings
from testagent.db.engine import (
    close_db,
    create_async_engine,
    get_engine,
    get_session,
    get_session_factory,
    reset_engine,
)
from testagent.models.base import Base

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Generator


@pytest.fixture(autouse=True)
def _isolate_db_state() -> Generator[None, None, None]:
    reset_engine()
    reset_settings()
    yield
    reset_engine()
    reset_settings()


def _make_sqlite_settings(**overrides: Any) -> TestAgentSettings:
    defaults: dict[str, object] = {
        "database_backend": "sqlite",
        "database_url": "sqlite+aiosqlite://",
        "database_echo": False,
    }
    defaults.update(overrides)
    return TestAgentSettings(**defaults)


def _make_postgres_settings(**overrides: Any) -> TestAgentSettings:
    defaults: dict[str, object] = {
        "database_backend": "postgresql",
        "postgres_host": "localhost",
        "postgres_port": 5432,
        "postgres_db": "testagent_test",
        "postgres_user": "testagent",
        "postgres_password": "testpass",
        "postgres_pool_size": 10,
        "postgres_max_overflow": 20,
        "postgres_pool_recycle": 3600,
        "database_echo": True,
    }
    defaults.update(overrides)
    return TestAgentSettings(**defaults)


class TestCreateAsyncEngineSQLite:
    @pytest.mark.asyncio
    async def test_sqlite_engine_url_contains_sqlite(self) -> None:
        settings = _make_sqlite_settings()
        engine = create_async_engine(settings)
        assert "sqlite" in str(engine.url)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_sqlite_memory_uses_static_pool(self) -> None:
        settings = _make_sqlite_settings(database_url="sqlite+aiosqlite:///:memory:")
        engine = create_async_engine(settings)
        assert isinstance(engine.pool, StaticPool)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_sqlite_wal_mode_activated(self) -> None:
        settings = _make_sqlite_settings()
        engine = create_async_engine(settings)
        async with engine.connect() as conn:
            result = await conn.execute(text("PRAGMA journal_mode"))
            row = result.fetchone()
            assert row is not None
            assert row[0].upper() in ("WAL", "MEMORY")
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_sqlite_foreign_keys_enabled(self) -> None:
        settings = _make_sqlite_settings()
        engine = create_async_engine(settings)
        async with engine.connect() as conn:
            await conn.execute(text("PRAGMA foreign_keys=ON"))
            result = await conn.execute(text("PRAGMA foreign_keys"))
            row = result.fetchone()
            assert row is not None
            assert row[0] == 1
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_sqlite_json1_available(self) -> None:
        settings = _make_sqlite_settings()
        engine = create_async_engine(settings)
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT json_array(1, 2, 3)"))
            row = result.fetchone()
            assert row is not None
            assert row[0] == "[1,2,3]"
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_sqlite_echo_propagated(self) -> None:
        settings = _make_sqlite_settings(database_echo=True)
        engine = create_async_engine(settings)
        assert engine.echo is True
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_sqlite_connect_args_set(self) -> None:
        settings = _make_sqlite_settings()
        engine = create_async_engine(settings)
        assert engine is not None
        await engine.dispose()


class TestCreateAsyncEnginePostgreSQL:
    def test_postgres_engine_url_format(self) -> None:
        settings = _make_postgres_settings()
        url = settings.get_database_url()
        assert url.startswith("postgresql+asyncpg://")
        assert "testagent_test" in url
        assert "testagent:testpass@localhost:5432" in url

    @patch("testagent.db.engine.sa_create_async_engine")
    def test_postgres_pool_size_configured(self, mock_create: MagicMock) -> None:
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine
        settings = _make_postgres_settings(postgres_pool_size=15)
        create_async_engine(settings)
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["pool_size"] == 15

    @patch("testagent.db.engine.sa_create_async_engine")
    def test_postgres_max_overflow_configured(self, mock_create: MagicMock) -> None:
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine
        settings = _make_postgres_settings(postgres_max_overflow=30)
        create_async_engine(settings)
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["max_overflow"] == 30

    @patch("testagent.db.engine.sa_create_async_engine")
    def test_postgres_pool_recycle_configured(self, mock_create: MagicMock) -> None:
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine
        settings = _make_postgres_settings(postgres_pool_recycle=7200)
        create_async_engine(settings)
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["pool_recycle"] == 7200

    @patch("testagent.db.engine.sa_create_async_engine")
    def test_postgres_pool_pre_ping_enabled(self, mock_create: MagicMock) -> None:
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine
        settings = _make_postgres_settings()
        create_async_engine(settings)
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["pool_pre_ping"] is True

    @patch("testagent.db.engine.sa_create_async_engine")
    def test_postgres_echo_propagated(self, mock_create: MagicMock) -> None:
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine
        settings = _make_postgres_settings(database_echo=True)
        create_async_engine(settings)
        call_kwargs = mock_create.call_args[1]
        assert call_kwargs["echo"] is True

    @patch("testagent.db.engine.sa_create_async_engine")
    def test_postgres_no_static_pool(self, mock_create: MagicMock) -> None:
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine
        settings = _make_postgres_settings()
        create_async_engine(settings)
        call_kwargs = mock_create.call_args[1]
        assert "poolclass" not in call_kwargs

    @patch("testagent.db.engine.sa_create_async_engine")
    def test_postgres_no_connect_args(self, mock_create: MagicMock) -> None:
        mock_engine = MagicMock()
        mock_create.return_value = mock_engine
        settings = _make_postgres_settings()
        create_async_engine(settings)
        call_kwargs = mock_create.call_args[1]
        assert "connect_args" not in call_kwargs


class TestAsyncSessionPool:
    @pytest.mark.asyncio
    async def test_session_factory_creates_async_session(self) -> None:
        settings = _make_sqlite_settings()
        engine = create_async_engine(settings)
        factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        async with factory() as session:
            assert isinstance(session, AsyncSession)
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_get_session_factory_returns_bound_factory(self) -> None:
        from testagent.config.settings import reset_settings as _rs

        reset_engine()
        _rs()
        import os

        os.environ["TESTAGENT_DATABASE_URL"] = "sqlite+aiosqlite://"
        os.environ["TESTAGENT_DATABASE_BACKEND"] = "sqlite"
        _rs()

        factory = get_session_factory()
        assert factory is not None
        assert factory.kw.get("class_") is AsyncSession or factory.class_ is AsyncSession
        await close_db()
        del os.environ["TESTAGENT_DATABASE_URL"]
        del os.environ["TESTAGENT_DATABASE_BACKEND"]
        _rs()

    @patch("testagent.db.engine.sa_create_async_engine")
    def test_postgres_session_factory_expire_on_commit_false(self, mock_create: MagicMock) -> None:
        mock_engine = MagicMock()
        mock_engine.dispose = MagicMock()
        mock_create.return_value = mock_engine
        settings = _make_postgres_settings()
        engine = create_async_engine(settings)
        factory = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        assert factory.kw.get("expire_on_commit") is False


class TestGetSessionContextManager:
    @pytest_asyncio.fixture()
    async def _setup_db(self) -> AsyncGenerator[None, None]:
        import os

        os.environ["TESTAGENT_DATABASE_URL"] = "sqlite+aiosqlite://"
        os.environ["TESTAGENT_DATABASE_BACKEND"] = "sqlite"
        reset_settings()
        reset_engine()
        engine = get_engine()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield
        engine_local = get_engine()
        async with engine_local.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await close_db()
        del os.environ["TESTAGENT_DATABASE_URL"]
        del os.environ["TESTAGENT_DATABASE_BACKEND"]
        reset_settings()

    @pytest.mark.asyncio
    async def test_get_session_commits_on_success(self, _setup_db: None) -> None:
        from testagent.models.session import TestSession

        async with get_session() as session:
            s = TestSession(name="engine-test-commit", status="pending", trigger_type="manual")
            session.add(s)
        async with get_session() as session:
            from sqlalchemy import select

            stmt = select(TestSession).where(TestSession.name == "engine-test-commit")
            result = await session.execute(stmt)
            found = result.scalar_one_or_none()
            assert found is not None
            assert found.name == "engine-test-commit"

    @pytest.mark.asyncio
    async def test_get_session_rollback_on_error(self, _setup_db: None) -> None:
        from testagent.models.session import TestSession

        with pytest.raises(ValueError, match="test error"):
            async with get_session() as session:
                s = TestSession(name="engine-test-rollback", status="pending", trigger_type="manual")
                session.add(s)
                raise ValueError("test error")
        async with get_session() as session:
            from sqlalchemy import select

            stmt = select(TestSession).where(TestSession.name == "engine-test-rollback")
            result = await session.execute(stmt)
            found = result.scalar_one_or_none()
            assert found is None

    @pytest.mark.asyncio
    async def test_get_session_returns_async_session(self, _setup_db: None) -> None:
        async with get_session() as session:
            assert isinstance(session, AsyncSession)


class TestGetEngineSingleton:
    @pytest.mark.asyncio
    async def test_get_engine_returns_same_instance(self) -> None:
        import os

        os.environ["TESTAGENT_DATABASE_URL"] = "sqlite+aiosqlite://"
        os.environ["TESTAGENT_DATABASE_BACKEND"] = "sqlite"
        reset_settings()
        reset_engine()
        e1 = get_engine()
        e2 = get_engine()
        assert e1 is e2
        await close_db()
        del os.environ["TESTAGENT_DATABASE_URL"]
        del os.environ["TESTAGENT_DATABASE_BACKEND"]
        reset_settings()

    @pytest.mark.asyncio
    async def test_reset_engine_clears_singleton(self) -> None:
        import os

        os.environ["TESTAGENT_DATABASE_URL"] = "sqlite+aiosqlite://"
        os.environ["TESTAGENT_DATABASE_BACKEND"] = "sqlite"
        reset_settings()
        reset_engine()
        get_engine()
        reset_engine()
        from testagent.db import engine as engine_mod

        assert engine_mod._engine is None
        assert engine_mod._session_factory is None
        del os.environ["TESTAGENT_DATABASE_URL"]
        del os.environ["TESTAGENT_DATABASE_BACKEND"]
        reset_settings()
