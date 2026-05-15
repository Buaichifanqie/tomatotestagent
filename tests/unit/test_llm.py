from __future__ import annotations

import asyncio
import heapq
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import SecretStr

from testagent.common.errors import LLMError, LLMRateLimitError, LLMTokenLimitError
from testagent.config.settings import TestAgentSettings
from testagent.llm.base import (
    PRIORITY_ANALYZER,
    PRIORITY_EXECUTOR,
    PRIORITY_PLANNER,
    VALID_STOP_REASONS,
    BudgetManager,
    ILLMProvider,
    LLMResponse,
    RateLimiter,
)
from testagent.llm.local_provider import LLMProviderFactory, LocalProvider
from testagent.llm.openai_provider import OpenAIProvider


def _make_settings(**overrides: Any) -> TestAgentSettings:
    defaults: dict[str, Any] = {
        "llm_provider": "openai",
        "openai_api_key": SecretStr("sk-test-key-12345678"),
        "openai_model": "gpt-4o",
        "local_model_url": "http://localhost:11434",
        "embedding_model": "BAAI/bge-large-zh-v1.5",
        "openai_embedding_model": "text-embedding-3-small",
    }
    defaults.update(overrides)
    return TestAgentSettings(**defaults)


def _make_chat_response(
    content_text: str = "Hello",
    tool_calls: list[dict] | None = None,
    finish_reason: str = "stop",
    input_tokens: int = 10,
    output_tokens: int = 20,
) -> dict:
    message: dict[str, Any] = {"role": "assistant"}
    if content_text:
        message["content"] = content_text
    else:
        message["content"] = None
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"message": message, "finish_reason": finish_reason, "index": 0}],
        "usage": {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
    }


def _make_embed_response(embeddings: list[list[float]]) -> dict:
    return {
        "data": [{"embedding": emb, "index": i} for i, emb in enumerate(embeddings)],
        "model": "text-embedding-3-small",
        "usage": {"prompt_tokens": 5, "total_tokens": 5},
    }


class TestLLMResponse:
    def test_create_basic_response(self) -> None:
        resp = LLMResponse(
            content=[{"type": "text", "text": "hi"}],
            stop_reason="end_turn",
            usage={"input_tokens": 5, "output_tokens": 3},
        )
        assert len(resp.content) == 1
        assert resp.content[0]["type"] == "text"
        assert resp.stop_reason == "end_turn"

    def test_create_tool_use_response(self) -> None:
        resp = LLMResponse(
            content=[
                {"type": "text", "text": "calling tool"},
                {"type": "tool_use", "id": "tc_1", "name": "search", "input": "{}"},
            ],
            stop_reason="tool_use",
            usage={"input_tokens": 10, "output_tokens": 20},
        )
        assert len(resp.content) == 2
        assert resp.content[1]["type"] == "tool_use"
        assert resp.stop_reason == "tool_use"

    def test_valid_stop_reasons(self) -> None:
        assert "end_turn" in VALID_STOP_REASONS
        assert "tool_use" in VALID_STOP_REASONS
        assert "max_tokens" in VALID_STOP_REASONS


class TestILLMProviderProtocol:
    def test_protocol_is_runtime_checkable(self) -> None:
        assert isinstance(OpenAIProvider, type)
        assert issubclass(OpenAIProvider, ILLMProvider)

    def test_local_provider_satisfies_protocol(self) -> None:
        assert issubclass(LocalProvider, ILLMProvider)

    def test_custom_class_satisfies_protocol(self) -> None:
        class DummyProvider:
            async def chat(
                self,
                system: str,
                messages: list[dict],
                tools: list[dict] | None = None,
                max_tokens: int = 4096,
                temperature: float = 0.7,
            ) -> LLMResponse:
                return LLMResponse(content=[], stop_reason="end_turn", usage={})

            async def embed(self, text: str) -> list[float]:
                return [0.0]

            async def embed_batch(self, texts: list[str]) -> list[list[float]]:
                return [[0.0]]

        assert isinstance(DummyProvider(), ILLMProvider)

    def test_incomplete_class_does_not_satisfy_protocol(self) -> None:
        class IncompleteProvider:
            async def chat(self, system: str, messages: list[dict]) -> LLMResponse:
                return LLMResponse(content=[], stop_reason="end_turn", usage={})

        assert not isinstance(IncompleteProvider(), ILLMProvider)


class TestPriorityConstants:
    def test_planner_has_highest_priority(self) -> None:
        assert PRIORITY_PLANNER == 0
        assert PRIORITY_PLANNER < PRIORITY_EXECUTOR
        assert PRIORITY_EXECUTOR < PRIORITY_ANALYZER

    def test_priority_ordering(self) -> None:
        priorities = [PRIORITY_PLANNER, PRIORITY_EXECUTOR, PRIORITY_ANALYZER]
        assert priorities == sorted(priorities)


class TestRateLimiter:
    def test_init_defaults(self) -> None:
        rl = RateLimiter()
        assert rl._rpm == 60
        assert rl._tpm == 100000

    def test_init_custom(self) -> None:
        rl = RateLimiter(rpm=30, tpm=50000)
        assert rl._rpm == 30
        assert rl._tpm == 50000

    async def test_acquire_below_capacity(self) -> None:
        rl = RateLimiter(rpm=10)
        await rl.acquire()
        assert rl._tokens < 10.0

    async def test_acquire_multiple_below_capacity(self) -> None:
        rl = RateLimiter(rpm=100)
        for _ in range(5):
            await rl.acquire()
        assert rl._tokens < 100.0

    async def test_acquire_with_priority_uses_priority_queue(self) -> None:
        rl = RateLimiter(rpm=1)
        await rl.acquire(priority=PRIORITY_PLANNER)

    async def test_acquire_priority_ordering(self) -> None:
        rl = RateLimiter(rpm=1)
        await rl.acquire(priority=PRIORITY_PLANNER)

        async with rl._lock:
            rl._refill()
            for priority in [PRIORITY_ANALYZER, PRIORITY_EXECUTOR, PRIORITY_PLANNER]:
                loop = asyncio.get_event_loop()
                future = loop.create_future()
                heapq.heappush(rl._waiters, (priority, rl._seq, future))
                rl._seq += 1

        popped_priorities: list[int] = []
        while rl._waiters:
            pri, _, future = heapq.heappop(rl._waiters)
            popped_priorities.append(pri)
            if not future.done():
                future.cancel()

        assert popped_priorities == [PRIORITY_PLANNER, PRIORITY_EXECUTOR, PRIORITY_ANALYZER]


class TestBudgetManager:
    def test_initial_state(self) -> None:
        bm = BudgetManager(total_budget=1000)
        assert bm.remaining == 1000
        assert bm.is_exhausted is False

    def test_consume_reduces_remaining(self) -> None:
        bm = BudgetManager(total_budget=1000)
        asyncio.run(bm.consume(300))
        assert bm.remaining == 700
        assert bm.is_exhausted is False

    def test_consume_to_exhaustion(self) -> None:
        bm = BudgetManager(total_budget=100)
        asyncio.run(bm.consume(100))
        assert bm.remaining == 0
        assert bm.is_exhausted is True

    async def test_planner_can_proceed_when_exhausted(self) -> None:
        bm = BudgetManager(total_budget=100)
        await bm.consume(100)
        assert bm.is_exhausted is True
        await bm.consume(10, priority=PRIORITY_PLANNER)
        assert bm.remaining == 0

    async def test_non_planner_blocked_when_exhausted(self) -> None:
        bm = BudgetManager(total_budget=100)
        await bm.consume(100)
        with pytest.raises(LLMTokenLimitError) as exc_info:
            await bm.consume(10, priority=PRIORITY_EXECUTOR)
        assert exc_info.value.code == "BUDGET_EXHAUSTED"

    async def test_analyzer_blocked_when_exhausted(self) -> None:
        bm = BudgetManager(total_budget=50)
        await bm.consume(50)
        with pytest.raises(LLMTokenLimitError) as exc_info:
            await bm.consume(10, priority=PRIORITY_ANALYZER)
        assert exc_info.value.code == "BUDGET_EXHAUSTED"


class TestOpenAIProvider:
    def test_init_with_secret_key(self) -> None:
        settings = _make_settings()
        provider = OpenAIProvider(settings)
        assert provider._api_key == "sk-test-key-12345678"
        assert provider._model == "gpt-4o"

    def test_init_without_key_raises(self) -> None:
        settings = _make_settings(openai_api_key=SecretStr(""))
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(LLMError) as exc_info:
                OpenAIProvider(settings)
            assert exc_info.value.code == "API_KEY_MISSING"

    def test_init_key_from_env(self) -> None:
        settings = _make_settings(openai_api_key=SecretStr(""))
        with patch.dict("os.environ", {"TESTAGENT_OPENAI_API_KEY": "sk-env-key-12345"}, clear=False):
            provider = OpenAIProvider(settings)
            assert provider._api_key == "sk-env-key-12345"

    async def test_chat_success(self) -> None:
        settings = _make_settings()
        provider = OpenAIProvider(settings)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _make_chat_response("Test reply")
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.chat("system", [{"role": "user", "content": "hi"}])

        assert isinstance(result, LLMResponse)
        assert result.stop_reason == "end_turn"
        assert len(result.content) == 1
        assert result.content[0]["text"] == "Test reply"

    async def test_chat_tool_use(self) -> None:
        settings = _make_settings()
        provider = OpenAIProvider(settings)
        tool_calls = [
            {
                "id": "tc_1",
                "type": "function",
                "function": {"name": "search", "arguments": '{"q": "test"}'},
            }
        ]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _make_chat_response(None, tool_calls=tool_calls, finish_reason="tool_calls")
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.chat("system", [{"role": "user", "content": "search"}])

        assert result.stop_reason == "tool_use"
        assert result.content[0]["type"] == "tool_use"
        assert result.content[0]["name"] == "search"

    async def test_chat_max_tokens(self) -> None:
        settings = _make_settings()
        provider = OpenAIProvider(settings)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _make_chat_response("truncated", finish_reason="length")
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.chat("system", [{"role": "user", "content": "long"}])

        assert result.stop_reason == "max_tokens"

    async def test_chat_429_retry(self) -> None:
        settings = _make_settings()
        provider = OpenAIProvider(settings)
        provider._rate_limiter = RateLimiter(rpm=1000)

        mock_429 = MagicMock()
        mock_429.status_code = 429
        mock_429.headers = {"retry-after": "0.01"}

        mock_success = MagicMock()
        mock_success.status_code = 200
        mock_success.json.return_value = _make_chat_response("after retry")
        mock_success.raise_for_status = MagicMock()

        call_count = 0

        async def _mock_post(url: str, json: dict) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return mock_429
            return mock_success

        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_client.is_closed = False

        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.chat("system", [{"role": "user", "content": "retry test"}])

        assert isinstance(result, LLMResponse)
        assert call_count == 3

    async def test_chat_429_exhausts_retries(self) -> None:
        settings = _make_settings()
        provider = OpenAIProvider(settings)
        provider._rate_limiter = RateLimiter(rpm=1000)

        mock_429 = MagicMock()
        mock_429.status_code = 429
        mock_429.headers = {"retry-after": "0.01"}
        mock_429.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_429)
        mock_client.is_closed = False

        with patch.object(provider, "_get_client", return_value=mock_client):
            with pytest.raises(LLMRateLimitError) as exc_info:
                await provider.chat("system", [{"role": "user", "content": "fail"}])
            assert exc_info.value.code == "RATE_LIMIT_EXCEEDED"

    async def test_chat_503_retry(self) -> None:
        settings = _make_settings()
        provider = OpenAIProvider(settings)
        provider._rate_limiter = RateLimiter(rpm=1000)

        mock_503 = MagicMock()
        mock_503.status_code = 503
        mock_503.text = "Service Unavailable"

        mock_success = MagicMock()
        mock_success.status_code = 200
        mock_success.json.return_value = _make_chat_response("after 503")
        mock_success.raise_for_status = MagicMock()

        call_count = 0

        async def _mock_post(url: str, json: dict) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_503
            return mock_success

        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_client.is_closed = False

        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.chat("system", [{"role": "user", "content": "retry"}])

        assert isinstance(result, LLMResponse)
        assert result.content[0]["text"] == "after 503"

    async def test_chat_timeout_retry(self) -> None:
        settings = _make_settings()
        provider = OpenAIProvider(settings)
        provider._rate_limiter = RateLimiter(rpm=1000)

        call_count = 0

        async def _mock_post(url: str, json: dict) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise httpx.TimeoutException("timeout")
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = _make_chat_response("after timeout")
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_client.is_closed = False

        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.chat("system", [{"role": "user", "content": "slow"}])

        assert isinstance(result, LLMResponse)

    async def test_chat_non_retryable_error(self) -> None:
        settings = _make_settings()
        provider = OpenAIProvider(settings)
        provider._rate_limiter = RateLimiter(rpm=1000)

        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.HTTPStatusError("401", request=MagicMock(), response=mock_resp))
        mock_client.is_closed = False

        with patch.object(provider, "_get_client", return_value=mock_client):
            with pytest.raises(LLMError) as exc_info:
                await provider.chat("system", [{"role": "user", "content": "bad key"}])
            assert exc_info.value.code == "API_ERROR"

    async def test_embed_single(self) -> None:
        settings = _make_settings()
        provider = OpenAIProvider(settings)
        embedding = [0.1] * 1536
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _make_embed_response([embedding])
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.embed("test text")

        assert len(result) == 1536

    async def test_embed_batch(self) -> None:
        settings = _make_settings()
        provider = OpenAIProvider(settings)
        emb1 = [0.1] * 1536
        emb2 = [0.2] * 1536
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _make_embed_response([emb1, emb2])
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.embed_batch(["text1", "text2"])

        assert len(result) == 2
        assert len(result[0]) == 1536
        assert len(result[1]) == 1536

    async def test_embed_returns_correct_dimension(self) -> None:
        settings = _make_settings()
        provider = OpenAIProvider(settings)
        dim = 3072
        embedding = [0.5] * dim
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _make_embed_response([embedding])
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.embed("dim test")

        assert len(result) == dim

    async def test_close_client(self) -> None:
        settings = _make_settings()
        provider = OpenAIProvider(settings)
        mock_client = AsyncMock()
        mock_client.is_closed = False
        provider._client = mock_client
        await provider.close()
        mock_client.aclose.assert_awaited_once()
        assert provider._client is None

    def test_map_stop_reason(self) -> None:
        assert OpenAIProvider._map_stop_reason("stop", False) == "end_turn"
        assert OpenAIProvider._map_stop_reason("tool_calls", True) == "tool_use"
        assert OpenAIProvider._map_stop_reason("stop", True) == "tool_use"
        assert OpenAIProvider._map_stop_reason("length", False) == "max_tokens"

    async def test_chat_with_tools_parameter(self) -> None:
        settings = _make_settings()
        provider = OpenAIProvider(settings)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _make_chat_response("tool response")
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        tools = [{"name": "search", "description": "Search the web", "parameters": {}}]

        with patch.object(provider, "_get_client", return_value=mock_client):
            await provider.chat(
                "system",
                [{"role": "user", "content": "search"}],
                tools=tools,
            )

        call_args = mock_client.post.call_args
        payload = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
        assert "tools" in payload
        assert payload["tools"][0]["type"] == "function"


class TestLocalProvider:
    def test_init(self) -> None:
        settings = _make_settings()
        provider = LocalProvider(settings)
        assert provider._base_url == "http://localhost:11434"

    async def test_chat_success(self) -> None:
        settings = _make_settings()
        provider = LocalProvider(settings)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _make_chat_response("local reply")
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.chat("system", [{"role": "user", "content": "hi"}])

        assert isinstance(result, LLMResponse)
        assert result.content[0]["text"] == "local reply"

    async def test_chat_connect_error_retry(self) -> None:
        settings = _make_settings()
        provider = LocalProvider(settings)
        provider._rate_limiter = RateLimiter(rpm=1000)

        call_count = 0

        async def _mock_post(url: str, json: dict) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise httpx.ConnectError("Connection refused")
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = _make_chat_response("reconnected")
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_client.is_closed = False

        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.chat("system", [{"role": "user", "content": "retry"}])

        assert isinstance(result, LLMResponse)

    async def test_embed_single(self) -> None:
        settings = _make_settings()
        provider = LocalProvider(settings)
        embedding = [0.1] * 1024
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _make_embed_response([embedding])
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.embed("test")

        assert len(result) == 1024

    async def test_embed_batch(self) -> None:
        settings = _make_settings()
        provider = LocalProvider(settings)
        emb1 = [0.1] * 1024
        emb2 = [0.2] * 1024
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _make_embed_response([emb1, emb2])
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch.object(provider, "_get_client", return_value=mock_client):
            result = await provider.embed_batch(["text1", "text2"])

        assert len(result) == 2
        assert len(result[0]) == 1024

    def test_map_stop_reason(self) -> None:
        assert LocalProvider._map_stop_reason("stop", False) == "end_turn"
        assert LocalProvider._map_stop_reason("tool_calls", True) == "tool_use"
        assert LocalProvider._map_stop_reason("length", False) == "max_tokens"

    async def test_close_client(self) -> None:
        settings = _make_settings()
        provider = LocalProvider(settings)
        mock_client = AsyncMock()
        mock_client.is_closed = False
        provider._client = mock_client
        await provider.close()
        mock_client.aclose.assert_awaited_once()
        assert provider._client is None


class TestLLMProviderFactory:
    def test_create_openai(self) -> None:
        settings = _make_settings(llm_provider="openai")
        provider = LLMProviderFactory.create(settings)
        assert isinstance(provider, OpenAIProvider)

    def test_create_local(self) -> None:
        settings = _make_settings(llm_provider="local")
        provider = LLMProviderFactory.create(settings)
        assert isinstance(provider, LocalProvider)

    def test_create_ollama(self) -> None:
        settings = _make_settings(llm_provider="ollama")
        provider = LLMProviderFactory.create(settings)
        assert isinstance(provider, LocalProvider)

    def test_create_unknown_raises(self) -> None:
        settings = _make_settings(llm_provider="unknown_provider")
        with pytest.raises(LLMError) as exc_info:
            LLMProviderFactory.create(settings)
        assert exc_info.value.code == "UNKNOWN_PROVIDER"

    def test_create_case_insensitive(self) -> None:
        settings = _make_settings(llm_provider="OpenAI")
        provider = LLMProviderFactory.create(settings)
        assert isinstance(provider, OpenAIProvider)

    def test_register_custom_provider(self) -> None:
        class CustomProvider:
            def __init__(self, settings: TestAgentSettings) -> None:
                self.settings = settings

            async def chat(
                self,
                system: str,
                messages: list[dict],
                tools: list[dict] | None = None,
                max_tokens: int = 4096,
                temperature: float = 0.7,
            ) -> LLMResponse:
                return LLMResponse(content=[], stop_reason="end_turn", usage={})

            async def embed(self, text: str) -> list[float]:
                return [0.0]

            async def embed_batch(self, texts: list[str]) -> list[list[float]]:
                return [[0.0]]

        LLMProviderFactory.register("custom", CustomProvider)
        settings = _make_settings(llm_provider="custom")
        provider = LLMProviderFactory.create(settings)
        assert isinstance(provider, CustomProvider)

        if "custom" in LLMProviderFactory._PROVIDER_MAP:
            del LLMProviderFactory._PROVIDER_MAP["custom"]
