from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from testagent.common.logging import get_logger
from testagent.config.settings import get_settings

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import AsyncGenerator

logger = get_logger(__name__)

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _build_connect_args() -> dict[str, object]:
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


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is not None:
        return _engine
    settings = get_settings()
    url = settings.database_url
    is_sqlite = url.startswith("sqlite")
    connect_args = _build_connect_args() if is_sqlite else {}
    engine_kwargs: dict[str, object] = {
        "echo": settings.database_echo,
        "connect_args": connect_args,
    }
    if is_sqlite and ":memory:" in url:
        engine_kwargs["poolclass"] = StaticPool
    _engine = create_async_engine(url, **engine_kwargs)
    if is_sqlite:
        event.listen(_engine.sync_engine, "connect", _set_sqlite_pragmas_sync)
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
