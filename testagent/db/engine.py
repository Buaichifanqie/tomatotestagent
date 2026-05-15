from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy import event, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine as sa_create_async_engine
from sqlalchemy.pool import StaticPool

from testagent.common.logging import get_logger
from testagent.config.settings import TestAgentSettings, get_settings

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import AsyncGenerator

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _build_sqlite_connect_args() -> dict[str, object]:
    return {"check_same_thread": False}


def _set_sqlite_pragmas_sync(dbapi_conn: sqlite3.Connection, _connection_record: object) -> None:
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        row = cursor.fetchone()
        if row and row[0].upper() != "WAL":
            logger.warning(
                "SQLite WAL mode not activated, got: %s",
                row[0],
                extra={"extra_data": {"pragma": "journal_mode", "value": row[0]}},
            )
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        has_json1 = False
        has_fts5 = False
        cursor.execute("PRAGMA compile_options")
        for opt_row in cursor.fetchall():
            opt = opt_row[0].upper()
            if "ENABLE_JSON1" in opt:
                has_json1 = True
            if "ENABLE_FTS5" in opt:
                has_fts5 = True
        if not has_json1:
            try:
                cursor.execute("SELECT json_array(1)")
                cursor.fetchone()
                has_json1 = True
            except Exception:
                logger.warning(
                    "SQLite JSON1 extension not available",
                    extra={"extra_data": {"extension": "JSON1"}},
                )
        if not has_fts5:
            logger.warning(
                "SQLite FTS5 extension not available",
                extra={"extra_data": {"extension": "FTS5"}},
            )
    except Exception as exc:
        logger.error(
            "Failed to set SQLite pragmas: %s",
            str(exc),
            extra={"extra_data": {"error": str(exc)}},
        )
    finally:
        cursor.close()


def create_async_engine(settings: TestAgentSettings) -> AsyncEngine:
    url = settings.get_database_url()
    is_sqlite = settings.database_backend == "sqlite"
    engine_kwargs: dict[str, object] = {
        "echo": settings.database_echo,
    }
    if is_sqlite:
        engine_kwargs["connect_args"] = _build_sqlite_connect_args()
        parsed_url = make_url(url)
        if parsed_url.database is None or ":memory:" in url:
            engine_kwargs["poolclass"] = StaticPool
    else:
        engine_kwargs["pool_size"] = settings.postgres_pool_size
        engine_kwargs["max_overflow"] = settings.postgres_max_overflow
        engine_kwargs["pool_recycle"] = settings.postgres_pool_recycle
        engine_kwargs["pool_pre_ping"] = True
    engine = sa_create_async_engine(url, **engine_kwargs)
    if is_sqlite:
        event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas_sync)
    logger.info(
        "Async engine created: backend=%s",
        settings.database_backend,
        extra={"extra_data": {"backend": settings.database_backend}},
    )
    return engine


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is not None:
        return _engine
    settings = get_settings()
    _engine = create_async_engine(settings)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is not None:
        return _session_factory
    engine = get_engine()
    _session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    engine = get_engine()
    from testagent.models.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database initialized successfully")


async def close_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("Database engine closed")


def reset_engine() -> None:
    global _engine, _session_factory
    _engine = None
    _session_factory = None


_executor_engines: dict[str, AsyncEngine] = {}
_executor_session_factories: dict[str, async_sessionmaker[AsyncSession]] = {}


def _build_executor_schema_url(settings: TestAgentSettings, executor_id: str) -> str:
    base_url = settings.get_database_url()
    if settings.database_backend == "postgresql":
        from urllib.parse import urlparse, urlunparse

        parsed = urlparse(base_url)
        path = parsed.path.rstrip("/")
        new_path = f"{path}_{executor_id}"
        return urlunparse(parsed._replace(path=new_path))
    return base_url


async def create_executor_schema(executor_id: str) -> AsyncEngine:
    settings = get_settings()
    if settings.database_backend != "postgresql":
        return get_engine()

    if executor_id in _executor_engines:
        return _executor_engines[executor_id]

    url = _build_executor_schema_url(settings, executor_id)
    engine = sa_create_async_engine(
        url,
        pool_size=settings.postgres_pool_size,
        max_overflow=settings.postgres_max_overflow,
        pool_recycle=settings.postgres_pool_recycle,
        pool_pre_ping=True,
        echo=settings.database_echo,
    )
    from testagent.models.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _executor_engines[executor_id] = engine
    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    _executor_session_factories[executor_id] = factory
    logger.info(
        "Executor schema created: executor_id=%s",
        executor_id,
        extra={"extra_data": {"executor_id": executor_id}},
    )
    return engine


async def get_executor_session(executor_id: str) -> AsyncGenerator[AsyncSession, None]:
    if executor_id not in _executor_session_factories:
        await create_executor_schema(executor_id)
    factory = _executor_session_factories[executor_id]
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def drop_executor_schema(executor_id: str) -> None:
    engine = _executor_engines.pop(executor_id, None)
    _executor_session_factories.pop(executor_id, None)
    if engine is not None:
        from testagent.models.base import Base

        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()
        logger.info(
            "Executor schema dropped: executor_id=%s",
            executor_id,
            extra={"extra_data": {"executor_id": executor_id}},
        )
