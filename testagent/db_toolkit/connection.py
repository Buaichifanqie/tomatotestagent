from __future__ import annotations

import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from testagent.common.logging import get_logger
from testagent.db_toolkit.errors import DbConnectionError

logger = get_logger(__name__)

_DIALECT_DRIVERS: dict[str, str] = {
    "mysql": "mysql+aiomysql",
    "postgresql": "postgresql+asyncpg",
    "sqlite": "sqlite+aiosqlite",
}


class ConnectionManager:
    """Manages async SQLAlchemy engines for the user's application database."""

    def __init__(self) -> None:
        self._engines: dict[str, AsyncEngine] = {}

    def _normalize_url(self, url: str) -> str:
        for short, driver in _DIALECT_DRIVERS.items():
            if url.startswith(f"{short}://"):
                return url.replace(f"{short}://", f"{driver}://", 1)
        return url

    def _create_engine(self, url: str) -> AsyncEngine:
        if url in self._engines:
            return self._engines[url]
        try:
            engine_kwargs: dict[str, Any] = {
                "pool_pre_ping": True,
            }
            if not url.startswith("sqlite"):
                engine_kwargs.update(
                    pool_size=5,
                    max_overflow=10,
                    pool_recycle=3600,
                )
            engine = create_async_engine(url, **engine_kwargs)
            self._engines[url] = engine
            logger.info("DB toolkit engine created for: %s", _mask_url(url))
            return engine
        except Exception as exc:
            raise DbConnectionError(
                f"Failed to create engine: {exc}",
                code="ENGINE_CREATE_FAILED",
                details={"url": _mask_url(url)},
            ) from exc

    async def get_engine(self, connection_url: str) -> AsyncEngine:
        url = self._normalize_url(connection_url)
        return self._create_engine(url)

    async def close(self, connection_url: str | None = None) -> None:
        if connection_url is None:
            for url, engine in self._engines.items():
                await engine.dispose()
                logger.info("DB toolkit engine closed: %s", _mask_url(url))
            self._engines.clear()
        else:
            url = self._normalize_url(connection_url)
            engine = self._engines.pop(url, None)
            if engine is not None:
                await engine.dispose()
                logger.info("DB toolkit engine closed: %s", _mask_url(url))


def _mask_url(url: str) -> str:
    return re.sub(r"://([^:]+):([^@]+)@", r"://\1:***@", url)
