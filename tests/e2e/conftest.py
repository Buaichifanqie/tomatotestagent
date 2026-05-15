from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

if TYPE_CHECKING:
    from collections.abc import Iterator

import pytest

from testagent.config.settings import reset_settings
from testagent.llm.base import LLMResponse


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TESTAGENT_DATABASE_URL", "sqlite+aiosqlite://")
    monkeypatch.setenv("TESTAGENT_OPENAI_API_KEY", "sk-test-mock-key")
    monkeypatch.setenv("TESTAGENT_OPENAI_MODEL", "gpt-4o-test")
    monkeypatch.setenv("TESTAGENT_LLM_PROVIDER", "openai")
    monkeypatch.setenv("TESTAGENT_AGENT_MAX_ROUNDS", "5")
    monkeypatch.setenv("TESTAGENT_APP_VERSION", "0.1.0-test")
    reset_settings()
    yield
    reset_settings()


@pytest.fixture()
def mock_llm_provider() -> MagicMock:
    mock = MagicMock()
    mock.chat = AsyncMock(
        return_value=LLMResponse(
            content=[{"type": "text", "text": "Mock response for e2e testing"}],
            stop_reason="end_turn",
            usage={"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
        )
    )
    mock.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    mock.embed_batch = AsyncMock(return_value=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
    return mock
