from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from testagent.config.settings import TestAgentSettings, reset_settings
from testagent.db.engine import reset_engine
from testagent.llm.base import ILLMProvider, LLMResponse
from testagent.models.base import Base

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TESTAGENT_DATABASE_URL", "sqlite+aiosqlite://")
    reset_settings()
    reset_engine()
    yield
    reset_settings()
    reset_engine()


@pytest.fixture()
def test_settings() -> TestAgentSettings:
    return TestAgentSettings(
        database_url="sqlite+aiosqlite://",
        llm_provider="openai",
        openai_api_key="sk-test-mock-key",
        openai_model="gpt-4o-mock",
        agent_max_rounds=5,
    )


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
async def async_db_session(async_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    factory = async_sessionmaker(
        bind=async_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with factory() as session:
        yield session


@pytest.fixture()
def mock_llm_provider() -> MagicMock:
    provider = MagicMock(spec=ILLMProvider)
    provider.chat = AsyncMock(
        return_value=LLMResponse(
            content=[{"type": "text", "text": "Test plan generated: 3 test tasks created."}],
            stop_reason="end_turn",
            usage={"input_tokens": 50, "output_tokens": 20},
        )
    )
    provider.embed = AsyncMock(return_value=[0.1] * 10)
    provider.embed_batch = AsyncMock(return_value=[[0.1] * 10])
    return provider


@pytest.fixture()
def mock_tool_use_llm_provider() -> MagicMock:
    first_response = LLMResponse(
        content=[
            {"type": "text", "text": "Searching for relevant test data..."},
            {"type": "tool_use", "name": "rag_query", "input": {"query": "login API docs"}},
        ],
        stop_reason="tool_use",
        usage={"input_tokens": 30, "output_tokens": 15},
    )
    second_response = LLMResponse(
        content=[{"type": "text", "text": "Based on the search results, I have created a test plan with 5 tasks."}],
        stop_reason="end_turn",
        usage={"input_tokens": 60, "output_tokens": 30},
    )
    provider = MagicMock(spec=ILLMProvider)
    provider.chat = AsyncMock(side_effect=[first_response, second_response])
    provider.embed = AsyncMock(return_value=[0.1] * 10)
    provider.embed_batch = AsyncMock(return_value=[[0.1] * 10])
    return provider
