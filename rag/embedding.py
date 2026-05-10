from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

import httpx

from testagent.common.errors import RAGDegradedError, RAGError
from testagent.common.logging import get_logger

if TYPE_CHECKING:
    from testagent.config.settings import TestAgentSettings

logger = get_logger(__name__)

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 2.0
_RETRY_STATUSES = {429, 502, 503, 504}

_LOCAL_DIMENSION = 1024
_API_DIMENSION = 1536
_MAX_TOKENS_PER_REQUEST = 2048
_MAX_BATCH_SIZE = 100


class KeyRotator:
    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise ValueError("At least one API key is required")
        self._keys = keys
        self._current_index = 0
        self._lock = asyncio.Lock()

    async def get_next_key(self) -> str:
        async with self._lock:
            key = self._keys[self._current_index]
            self._current_index = (self._current_index + 1) % len(self._keys)
            return key

    def get_current_key(self) -> str:
        return self._keys[self._current_index]

    @property
    def key_count(self) -> int:
        return len(self._keys)


@runtime_checkable
class IEmbeddingService(Protocol):
    async def embed(self, text: str) -> list[float]: ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...

    def get_dimension(self) -> int: ...


class LocalEmbeddingService:
    __test__ = False

    def __init__(self, model_name: str = "BAAI/bge-large-zh-v1.5") -> None:
        self._model_name = model_name
        self._model = self._load_model()

    def _load_model(self) -> Any:
        try:
            from sentence_transformers import SentenceTransformer

            return SentenceTransformer(self._model_name)
        except ImportError:
            raise RAGDegradedError(
                "sentence-transformers is not installed; Embedding service degraded to BM25",
                code="SENTENCE_TRANSFORMERS_NOT_INSTALLED",
                details={"model": self._model_name},
            ) from None
        except Exception as exc:
            raise RAGDegradedError(
                f"Failed to load local embedding model {self._model_name!r}: {exc}",
                code="LOCAL_MODEL_LOAD_FAILED",
                details={"model": self._model_name, "error": str(exc)},
            ) from exc

    async def embed(self, text: str) -> list[float]:
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        loop = asyncio.get_running_loop()
        embeddings = await loop.run_in_executor(None, self._model.encode, texts)
        return [emb.tolist() for emb in embeddings]

    def get_dimension(self) -> int:
        return _LOCAL_DIMENSION


class APIEmbeddingService:
    __test__ = False

    def __init__(
        self,
        api_key: str | None = None,
        api_keys: list[str] | None = None,
        model: str = "text-embedding-3-small",
    ) -> None:
        if api_keys:
            self._key_rotator = KeyRotator(api_keys)
        elif api_key:
            self._key_rotator = KeyRotator([api_key])
        else:
            raise ValueError("Either api_key or api_keys must be provided")

        self._model = model
        self._base_url = "https://api.openai.com/v1"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(60.0, connect=10.0),
            )
        return self._client

    async def _get_auth_headers(self) -> dict[str, str]:
        api_key = await self._key_rotator.get_next_key()
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _estimate_tokens(self, text: str) -> int:
        return len(text) // 4

    def _split_texts_by_token_limit(self, texts: list[str]) -> list[list[str]]:
        batches: list[list[str]] = []
        current_batch: list[str] = []
        current_tokens = 0

        for text in texts:
            text_tokens = self._estimate_tokens(text)

            if text_tokens > _MAX_TOKENS_PER_REQUEST:
                if current_batch:
                    batches.append(current_batch)
                    current_batch = []
                    current_tokens = 0
                batches.append([text])
                continue

            if current_tokens + text_tokens > _MAX_TOKENS_PER_REQUEST or len(current_batch) >= _MAX_BATCH_SIZE:
                if current_batch:
                    batches.append(current_batch)
                current_batch = [text]
                current_tokens = text_tokens
            else:
                current_batch.append(text)
                current_tokens += text_tokens

        if current_batch:
            batches.append(current_batch)

        return batches

    async def _send_request_with_retry(self, payload: dict[str, object]) -> list[list[float]]:
        last_exc: Exception | None = None

        for attempt in range(_MAX_RETRIES):
            try:
                client = await self._get_client()
                headers = await self._get_auth_headers()
                response = await client.post("/embeddings", json=payload, headers=headers)

                if response.status_code in _RETRY_STATUSES:
                    delay = _RETRY_BASE_DELAY * (2**attempt)
                    if response.status_code == 429:
                        logger.warning(
                            "OpenAI Embedding API rate limited (429), retrying in %.1fs (attempt %d/%d)",
                            delay,
                            attempt + 1,
                            _MAX_RETRIES,
                        )
                    else:
                        logger.warning(
                            "OpenAI Embedding API %d, retrying in %.1fs (attempt %d/%d)",
                            response.status_code,
                            delay,
                            attempt + 1,
                            _MAX_RETRIES,
                        )
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                data = response.json()
                sorted_data = sorted(data["data"], key=lambda d: d["index"])
                return [item["embedding"] for item in sorted_data]

            except httpx.HTTPStatusError as exc:
                raise RAGError(
                    f"OpenAI Embedding API error: {exc.response.status_code}",
                    code="EMBED_API_ERROR",
                    details={"status_code": exc.response.status_code},
                ) from exc
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                delay = _RETRY_BASE_DELAY * (2**attempt)
                logger.warning(
                    "OpenAI Embedding API connection error (attempt %d/%d), retrying in %.1fs",
                    attempt + 1,
                    _MAX_RETRIES,
                    delay,
                )
                await asyncio.sleep(delay)

        raise RAGDegradedError(
            f"OpenAI Embedding API failed after {_MAX_RETRIES} retries",
            code="EMBED_API_DEGRADED",
            details={"last_error": str(last_exc)},
        )

    async def embed(self, text: str) -> list[float]:
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        batches = self._split_texts_by_token_limit(texts)
        all_embeddings: list[list[float]] = []

        for batch in batches:
            payload: dict[str, object] = {
                "model": self._model,
                "input": batch,
            }
            batch_embeddings = await self._send_request_with_retry(payload)
            all_embeddings.extend(batch_embeddings)

        return all_embeddings

    def get_dimension(self) -> int:
        return _API_DIMENSION


class EmbeddingFailover:
    __test__ = False

    def __init__(
        self,
        primary: IEmbeddingService,
        fallback: IEmbeddingService | None = None,
        circuit_breaker_threshold: int = 3,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._primary_failures = 0
        self._fallback_failures = 0
        self._circuit_breaker_threshold = circuit_breaker_threshold
        self._circuit_open = False

    @property
    def is_circuit_open(self) -> bool:
        return self._circuit_open

    @property
    def is_degraded(self) -> bool:
        return self._circuit_open

    def reset(self) -> None:
        self._primary_failures = 0
        self._fallback_failures = 0
        self._circuit_open = False

    async def _execute(
        self,
        method: str,
        arg: str | list[str],
    ) -> list[float] | list[list[float]]:
        if isinstance(arg, list) and not arg:
            return []

        is_batch = isinstance(arg, list)

        if self._circuit_open and self._fallback is not None:
            logger.info(
                "Circuit breaker open, using fallback embedding service directly (primary_failures=%d)",
                self._primary_failures,
            )
            fb_method = getattr(self._fallback, method)
            try:
                result = await fb_method(arg)
                return cast("list[float] | list[list[float]]", result)
            except Exception as fb_exc:
                self._fallback_failures += 1
                raise RAGDegradedError(
                    "Embedding service unavailable, degraded to pure BM25",
                    code="EMBED_SERVICE_DEGRADED",
                    details={
                        "fallback_error": str(fb_exc),
                        "primary_failures": self._primary_failures,
                        "fallback_failures": self._fallback_failures,
                        "circuit_open": self._circuit_open,
                    },
                ) from fb_exc

        primary_method = getattr(self._primary, method)
        try:
            result = await primary_method(arg)
            self._primary_failures = 0
            self._circuit_open = False
            return cast("list[float] | list[list[float]]", result)
        except Exception as primary_exc:
            logger.warning(
                "Primary embedding service %s failed: %s",
                "batch" if is_batch else "single",
                str(primary_exc),
                exc_info=primary_exc,
            )
            self._primary_failures += 1

            if self._primary_failures >= self._circuit_breaker_threshold:
                self._circuit_open = True

            if self._fallback is not None:
                fb_method = getattr(self._fallback, method)
                try:
                    result = await fb_method(arg)
                    self._fallback_failures = 0
                    return cast("list[float] | list[list[float]]", result)
                except Exception as fallback_exc:
                    logger.error(
                        "Fallback embedding service %s also failed: %s",
                        "batch" if is_batch else "single",
                        str(fallback_exc),
                        exc_info=fallback_exc,
                    )
                    self._fallback_failures += 1
                    raise RAGDegradedError(
                        "Embedding service unavailable, degraded to pure BM25",
                        code="EMBED_SERVICE_DEGRADED",
                        details={
                            "primary_error": str(primary_exc),
                            "fallback_error": str(fallback_exc),
                            "primary_failures": self._primary_failures,
                            "fallback_failures": self._fallback_failures,
                            "circuit_open": self._circuit_open,
                        },
                    ) from fallback_exc
            else:
                raise RAGDegradedError(
                    "Embedding service unavailable, degraded to pure BM25",
                    code="EMBED_SERVICE_DEGRADED",
                    details={
                        "primary_error": str(primary_exc),
                        "primary_failures": self._primary_failures,
                        "circuit_open": self._circuit_open,
                    },
                ) from primary_exc

    async def embed(self, text: str) -> list[float]:
        result = await self._execute("embed", text)
        return result  # type: ignore[return-value]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        result = await self._execute("embed_batch", texts)
        return result  # type: ignore[return-value]

    def get_dimension(self) -> int:
        return self._primary.get_dimension()

    @property
    def primary_failures(self) -> int:
        return self._primary_failures

    @property
    def fallback_failures(self) -> int:
        return self._fallback_failures


class EmbeddingFactory:
    __test__ = False

    @staticmethod
    def _load_api_keys(settings: TestAgentSettings) -> list[str]:
        from testagent.common.security import KeyManager

        keys: list[str] = []

        primary_key = settings.openai_api_key.get_secret_value()
        if primary_key:
            keys.append(primary_key)

        env_keys_str = os.environ.get("TESTAGENT_OPENAI_API_KEYS", "")
        if env_keys_str:
            for key in env_keys_str.split(","):
                key = key.strip()
                if key and key not in keys:
                    keys.append(key)

        if not keys:
            try:
                key = KeyManager.get_key("openai", "api_key")
                if key and key not in keys:
                    keys.append(key)
            except Exception:
                pass

        return keys

    @staticmethod
    def create(settings: TestAgentSettings) -> IEmbeddingService:
        mode = settings.embedding_mode.lower()

        if mode == "local":
            try:
                primary = LocalEmbeddingService(model_name=settings.embedding_model)
                return EmbeddingFailover(primary=primary, fallback=None)
            except RAGDegradedError:
                raise
            except Exception as exc:
                raise RAGDegradedError(
                    f"Failed to create local embedding service: {exc}",
                    code="LOCAL_EMBED_CREATE_FAILED",
                    details={"mode": mode, "error": str(exc)},
                ) from exc

        if mode == "api" or mode == "openai":
            api_keys = EmbeddingFactory._load_api_keys(settings)

            if not api_keys:
                raise RAGDegradedError(
                    "OpenAI API key not found; Embedding service degraded to BM25",
                    code="EMBED_API_KEY_MISSING",
                    details={"mode": mode},
                )

            try:
                _primary = APIEmbeddingService(
                    api_keys=api_keys,
                    model=settings.openai_embedding_model,
                )
            except RAGDegradedError:
                raise
            except Exception as exc:
                raise RAGDegradedError(
                    f"Failed to create API embedding service: {exc}",
                    code="API_EMBED_CREATE_FAILED",
                    details={"mode": mode, "error": str(exc)},
                ) from exc
            api_primary: IEmbeddingService = _primary

            api_fallback: IEmbeddingService | None = None
            try:
                api_fallback = LocalEmbeddingService(model_name=settings.embedding_model)
            except Exception as exc:
                logger.warning(
                    "Failed to create fallback local embedding service: %s",
                    str(exc),
                )

            return EmbeddingFailover(primary=api_primary, fallback=api_fallback)

        raise RAGDegradedError(
            f"Unknown embedding mode: {mode!r}",
            code="UNKNOWN_EMBEDDING_MODE",
            details={"mode": mode, "available": ["local", "api", "openai"]},
        )
