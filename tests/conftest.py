from __future__ import annotations

import os
import socket
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from testagent.config.settings import TestAgentSettings
from testagent.db.migrations import async_upgrade_head

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

PG_HOST = os.environ.get("TESTAGENT_PG_HOST", "localhost")
PG_PORT = int(os.environ.get("TESTAGENT_PG_PORT", "5432"))
PG_USER = os.environ.get("TESTAGENT_PG_USER", "testagent")
PG_PASSWORD = os.environ.get("TESTAGENT_PG_PASSWORD", "testagent")
PG_DB = os.environ.get("TESTAGENT_PG_DB", "testagent_test")

PG_URL = f"postgresql+asyncpg://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DB}"


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


@pytest.fixture
def postgres_settings() -> TestAgentSettings:
    return TestAgentSettings(
        database_backend="postgresql",
        database_url=PG_URL,
        postgres_host=PG_HOST,
        postgres_port=PG_PORT,
        postgres_db=PG_DB,
        postgres_user=PG_USER,
        postgres_password=SecretStr(PG_PASSWORD),
        postgres_pool_size=5,
        postgres_max_overflow=10,
        postgres_pool_recycle=3600,
    )


@pytest_asyncio.fixture
async def postgres_db_session() -> AsyncGenerator[AsyncSession, None]:
    if not _pg_is_available():
        pytest.skip("PostgreSQL not available")
    engine = create_async_engine(
        PG_URL,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )
    async with engine.begin() as conn:
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version CASCADE"))
    await async_upgrade_head(database_url=PG_URL)
    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with factory() as session:
        yield session
    table_names = [
        "defects",
        "test_results",
        "test_tasks",
        "test_plans",
        "mcp_configs",
        "skill_definitions",
        "test_sessions",
        "alembic_version",
    ]
    async with engine.begin() as conn:
        for tname in table_names:
            await conn.execute(text(f"DROP TABLE IF EXISTS {tname} CASCADE"))
    await engine.dispose()


@pytest.fixture(params=["sqlite", "postgresql"])
def backend(request: pytest.FixtureRequest) -> str:
    if request.param == "postgresql" and not _pg_is_available():
        pytest.skip("PostgreSQL not available")
    return str(request.param)
