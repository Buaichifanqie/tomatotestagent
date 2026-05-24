from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx

from testagent.common.errors import LLMError, LLMRateLimitError
from testagent.common.logging import get_logger
from testagent.common.security import KeyManager
from testagent.llm.base import (
    PRIORITY_EXECUTOR,
    BudgetManager,
    LLMResponse,
    RateLimiter,
)

if TYPE_CHECKING:
    from testagent.config.settings import TestAgentSettings

logger = get_logger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0
_RETRY_STATUSES = {429, 502, 503, 504}


class OpenAIProvider:
    __test__ = False

    def __init__(self, settings: TestAgentSettings) -> None:
        self._settings = settings
        self._api_key = self._resolve_api_key(settings)
        self._model = settings.openai_model
        self._base_url = settings.openai_base_url
        self._client: httpx.AsyncClient | None = None
        self._rate_limiter = RateLimiter()
        self._budget_manager = BudgetManager()

    def _resolve_api_key(self, settings: TestAgentSettings) -> str:
        raw = settings.openai_api_key.get_secret_value()
        if raw:
            return raw
        try:
            return KeyManager.get_key("openai", "api_key")
        except Exception as exc:
            raise LLMError(
                "OpenAI API key not found; set TESTAGENT_OPENAI_API_KEY env var or configure keyring",
                code="API_KEY_MISSING",
            ) from exc

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(180.0, connect=15.0),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def reset_budget(self) -> None:
        self._budget_manager.reset()

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
            "model": self._model,
            "messages": formatted_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": t} for t in tools]

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                client = await self._get_client()
                response = await client.post("/chat/completions", json=payload)
                if response.status_code == 429:
                    retry_after = float(response.headers.get("retry-after", _RETRY_BASE_DELAY * (2**attempt)))
                    logger.warning(
                        "OpenAI 429 rate limit, retrying in %.1fs (attempt %d/%d)",
                        retry_after,
                        attempt + 1,
                        _MAX_RETRIES,
                    )
                    await asyncio.sleep(retry_after)
                    continue
                if response.status_code in _RETRY_STATUSES and response.status_code != 429:
                    delay = _RETRY_BASE_DELAY * (2**attempt)
                    logger.warning(
                        "OpenAI %d error, retrying in %.1fs (attempt %d/%d)",
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
                if exc.response.status_code == 429:
                    retry_after = float(exc.response.headers.get("retry-after", _RETRY_BASE_DELAY * (2**attempt)))
                    await asyncio.sleep(retry_after)
                    continue
                if exc.response.status_code in _RETRY_STATUSES:
                    await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))
                    continue
                raise LLMError(
                    f"OpenAI API error: {exc.response.status_code} {exc.response.text}",
                    code="API_ERROR",
                    details={"status_code": exc.response.status_code},
                ) from exc
            except httpx.TimeoutException as exc:
                last_exc = exc
                delay = _RETRY_BASE_DELAY * (2**attempt)
                logger.warning("OpenAI timeout, retrying in %.1fs", delay)
                await asyncio.sleep(delay)
                continue

        raise LLMRateLimitError(
            f"OpenAI API failed after {_MAX_RETRIES} retries",
            code="RATE_LIMIT_EXCEEDED",
            details={"last_error": str(last_exc)},
        )

    def _parse_chat_response(self, data: dict[str, Any]) -> LLMResponse:
        choices = data.get("choices", [])
        choice = choices[0] if choices else {}
        if not isinstance(choice, dict):
            choice = {}
        message = choice.get("message", {}) if isinstance(choice, dict) else {}
        if not isinstance(message, dict):
            message = {}
        content: list[dict[str, Any]] = []

        if message.get("content"):
            content.append({"type": "text", "text": message["content"]})

        if message.get("tool_calls"):
            for tc in message["tool_calls"]:
                if not isinstance(tc, dict):
                    continue
                func = tc.get("function")
                if not isinstance(func, dict):
                    continue
                raw_args = func.get("arguments", "{}")
                if isinstance(raw_args, str):
                    import json as _json

                    try:
                        parsed_args: dict[str, object] = _json.loads(raw_args)
                    except Exception:
                        parsed_args = {"raw": raw_args}
                else:
                    parsed_args = raw_args
                content.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id", ""),
                        "name": func.get("name", ""),
                        "input": parsed_args,
                    }
                )

        finish_reason = choice.get("finish_reason", "stop") if isinstance(choice, dict) else "stop"
        stop_reason = self._map_stop_reason(finish_reason, bool(message.get("tool_calls") if isinstance(message, dict) else False))

        usage = data.get("usage", {})
        usage_dict = {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }

        return LLMResponse(
            content=content,
            stop_reason=stop_reason,
            usage=usage_dict,
            raw_message=dict(message),
        )

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

        payload: dict[str, Any] = {
            "model": self._settings.openai_embedding_model,
            "input": texts,
        }

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                client = await self._get_client()
                response = await client.post("/embeddings", json=payload)
                if response.status_code in _RETRY_STATUSES:
                    delay = _RETRY_BASE_DELAY * (2**attempt)
                    await asyncio.sleep(delay)
                    continue
                response.raise_for_status()
                data = response.json()
                sorted_data = sorted(data["data"], key=lambda d: d["index"])
                return [item["embedding"] for item in sorted_data]
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                if exc.response.status_code in _RETRY_STATUSES:
                    await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))
                    continue
                raise LLMError(
                    f"OpenAI Embedding API error: {exc.response.status_code}",
                    code="EMBED_API_ERROR",
                ) from exc
            except httpx.TimeoutException as exc:
                last_exc = exc
                await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))
                continue

        raise LLMRateLimitError(
            f"OpenAI Embedding API failed after {_MAX_RETRIES} retries",
            code="RATE_LIMIT_EXCEEDED",
            details={"last_error": str(last_exc)},
        )
