from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from testagent.common.logging import get_logger
from testagent.db_ops.errors import DbConnectionError

logger = get_logger(__name__)

# Supported dialect drivers
_DIALECT_DRIVERS: dict[str, str] = {
    "mysql": "mysql+aiomysql",
    "postgresql": "postgresql+asyncpg",
    "sqlite": "sqlite+aiosqlite",
}


class ConnectionManager:
    """Manages async SQLAlchemy connections to the user's application database.

    Unlike testagent.db.engine (internal project DB), this connects to
    the database under test (e.g. the app's MySQL/PostgreSQL/SQLite).
    """

    def __init__(self) -> None:
        self._engines: dict[str, AsyncEngine] = {}

    def _normalize_url(self, url: str) -> str:
        """Normalize shorthand dialect names to full driver URLs."""
        for short, driver in _DIALECT_DRIVERS.items():
            if url.startswith(f"{short}://"):
                return url.replace(f"{short}://", f"{driver}://", 1)
        return url

    async def get_engine(self, connection_url: str) -> AsyncEngine:
        """Get or create an async engine for the given connection URL."""
        url = self._normalize_url(connection_url)
        if url in self._engines:
            return self._engines[url]

        try:
            engine = create_async_engine(
                url,
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=10,
                pool_recycle=3600,
            )
            self._engines[url] = engine
            logger.info("DB ops engine created for: %s", _mask_url(url))
            return engine
        except Exception as exc:
            raise DbConnectionError(
                f"Failed to create engine: {exc}",
                code="ENGINE_CREATE_FAILED",
                details={"url": _mask_url(url)},
            ) from exc

    async def test_connection(self, connection_url: str) -> dict[str, Any]:
        """Test connectivity and return DB metadata (dialect, version)."""
        engine = await self.get_engine(connection_url)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text("SELECT 1"))
                result.scalar()

                dialect_name = engine.dialect.name
                version = engine.dialect.server_version_info
                return {
                    "connected": True,
                    "dialect": dialect_name,
                    "version": ".".join(str(v) for v in version) if version else "unknown",
                }
        except Exception as exc:
            raise DbConnectionError(
                f"Connection test failed: {exc}",
                code="CONNECTION_TEST_FAILED",
                details={"url": _mask_url(connection_url)},
            ) from exc

    async def get_connection(self, connection_url: str) -> AsyncConnection:
        """Get a raw async connection for the given URL."""
        engine = await self.get_engine(connection_url)
        try:
            return await engine.connect()
        except Exception as exc:
            raise DbConnectionError(
                f"Failed to connect: {exc}",
                code="CONNECTION_FAILED",
                details={"url": _mask_url(connection_url)},
            ) from exc

    async def close(self, connection_url: str | None = None) -> None:
        """Close engine(s). If url is None, close all."""
        if connection_url is None:
            for url, engine in self._engines.items():
                await engine.dispose()
                logger.info("DB ops engine closed: %s", _mask_url(url))
            self._engines.clear()
        else:
            url = self._normalize_url(connection_url)
            engine = self._engines.pop(url, None)
            if engine is not None:
                await engine.dispose()
                logger.info("DB ops engine closed: %s", _mask_url(url))


def _mask_url(url: str) -> str:
    """Mask password in connection URL for logging."""
    import re
    return re.sub(r"://([^:]+):([^@]+)@", r"//\1:***@", url)
