from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Callable

import httpx

from testagent.common.errors import LLMError, LLMRateLimitError
from testagent.common.logging import get_logger
from testagent.llm.base import (
    PRIORITY_EXECUTOR,
    BudgetManager,
    ILLMProvider,
    LLMResponse,
    RateLimiter,
)

if TYPE_CHECKING:
    from testagent.config.settings import TestAgentSettings

logger = get_logger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0
_RETRY_STATUSES = {429, 502, 503, 504}


class LocalProvider:
    __test__ = False

    def __init__(self, settings: TestAgentSettings) -> None:
        self._settings = settings
        self._base_url = settings.local_model_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._rate_limiter = RateLimiter()
        self._budget_manager = BudgetManager()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(120.0, connect=10.0),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def chat(
        self,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        priority: int = PRIORITY_EXECUTOR,
    ) -> LLMResponse:
        await self._rate_limiter.acquire(priority)

        formatted_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
        ]
        for msg in messages:
            formatted_messages.append(msg)

        payload: dict[str, Any] = {
            "messages": formatted_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                client = await self._get_client()
                response = await client.post("/v1/chat/completions", json=payload)
                if response.status_code in _RETRY_STATUSES:
                    delay = _RETRY_BASE_DELAY * (2**attempt)
                    logger.warning(
                        "Ollama %d error, retrying in %.1fs (attempt %d/%d)",
                        response.status_code,
                        delay,
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
                data = response.json()
                result = self._parse_chat_response(data)
                await self._budget_manager.consume(result.usage.get("total_tokens", 0), priority)
                return result
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code in _RETRY_STATUSES:
                    await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))
                    continue
                raise LLMError(
                    f"Ollama API error: {exc.response.status_code} {exc.response.text}",
                    code="LOCAL_API_ERROR",
                    details={"status_code": exc.response.status_code},
                ) from exc
            except httpx.TimeoutException as exc:
                last_exc = exc
                delay = _RETRY_BASE_DELAY * (2**attempt)
                logger.warning("Ollama timeout, retrying in %.1fs", delay)
                await asyncio.sleep(delay)
                continue
            except httpx.ConnectError as exc:
                last_exc = exc
                delay = _RETRY_BASE_DELAY * (2**attempt)
                logger.warning("Ollama connection error, retrying in %.1fs", delay)
                await asyncio.sleep(delay)
                continue

        raise LLMRateLimitError(
            f"Ollama API failed after {_MAX_RETRIES} retries",
            code="LOCAL_RATE_LIMIT_EXCEEDED",
            details={"last_error": str(last_exc)},
        )

    def _parse_chat_response(self, data: dict[str, Any]) -> LLMResponse:
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        content: list[dict[str, Any]] = []

        if message.get("content"):
            content.append({"type": "text", "text": message["content"]})

        if message.get("tool_calls"):
            for tc in message["tool_calls"]:
                func = tc.get("function", {})
                content.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "input": func.get("arguments", "{}"),
                    }
                )

        finish_reason = choice.get("finish_reason", "stop")
        has_tool_calls = bool(message.get("tool_calls"))
        stop_reason = self._map_stop_reason(finish_reason, has_tool_calls)

        usage = data.get("usage", {})
        usage_dict = {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }

        return LLMResponse(content=content, stop_reason=stop_reason, usage=usage_dict)

    @staticmethod
    def _map_stop_reason(finish_reason: str, has_tool_calls: bool) -> str:
        if has_tool_calls or finish_reason == "tool_calls":
            return "tool_use"
        if finish_reason == "length":
            return "max_tokens"
        return "end_turn"

    async def embed(self, text: str) -> list[float]:
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        await self._rate_limiter.acquire()

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                client = await self._get_client()
                payload: dict[str, Any] = {"model": self._settings.embedding_model, "input": texts}
                response = await client.post("/v1/embeddings", json=payload)
                if response.status_code in _RETRY_STATUSES:
                    await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))
                    continue
                response.raise_for_status()
                data = response.json()
                sorted_data = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
                return [item["embedding"] for item in sorted_data]
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code in _RETRY_STATUSES:
                    await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))
                    continue
                raise LLMError(
                    f"Ollama Embedding API error: {exc.response.status_code}",
                    code="LOCAL_EMBED_API_ERROR",
                ) from exc
            except httpx.TimeoutException as exc:
                last_exc = exc
                await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))
                continue
            except httpx.ConnectError as exc:
                last_exc = exc
                await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))
                continue

        raise LLMRateLimitError(
            f"Ollama Embedding API failed after {_MAX_RETRIES} retries",
            code="LOCAL_RATE_LIMIT_EXCEEDED",
            details={"last_error": str(last_exc)},
        )


class LLMProviderFactory:
    __test__ = False

    _PROVIDER_MAP: ClassVar[dict[str, Callable[[TestAgentSettings], ILLMProvider]]] = {}

    @classmethod
    def register(cls, name: str, provider_cls: Callable[[TestAgentSettings], ILLMProvider]) -> None:
        cls._PROVIDER_MAP[name] = provider_cls

    @classmethod
    def create(cls, settings: TestAgentSettings) -> ILLMProvider:
        provider_name = settings.llm_provider.lower()
        if provider_name == "openai":
            from testagent.llm.openai_provider import OpenAIProvider

            return OpenAIProvider(settings)
        if provider_name in ("local", "ollama"):
            return LocalProvider(settings)
        custom_cls = cls._PROVIDER_MAP.get(provider_name)
        if custom_cls is not None:
            return custom_cls(settings)
        available = ["openai", "local", "ollama", *list(cls._PROVIDER_MAP.keys())]
        raise LLMError(
            f"Unknown LLM provider: {provider_name!r}",
            code="UNKNOWN_PROVIDER",
            details={"available": available},
        )
