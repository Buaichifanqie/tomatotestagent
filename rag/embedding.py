from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

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

    def __init__(self, api_key: str, model: str = "text-embedding-3-small") -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = "https://api.openai.com/v1"
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(60.0, connect=10.0),
            )
        return self._client

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def embed(self, text: str) -> list[float]:
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload: dict[str, object] = {
            "model": self._model,
            "input": texts,
        }

        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                client = await self._get_client()
                response = await client.post("/embeddings", json=payload)
                if response.status_code in _RETRY_STATUSES:
                    delay = _RETRY_BASE_DELAY * (2**attempt)
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
                last_exc = exc
                if exc.response.status_code in _RETRY_STATUSES:
                    await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))
                    continue
                raise RAGError(
                    f"OpenAI Embedding API error: {exc.response.status_code}",
                    code="EMBED_API_ERROR",
                    details={"status_code": exc.response.status_code},
                ) from exc
            except httpx.TimeoutException as exc:
                last_exc = exc
                await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))
                continue
            except httpx.ConnectError as exc:
                last_exc = exc
                await asyncio.sleep(_RETRY_BASE_DELAY * (2**attempt))
                continue

        raise RAGDegradedError(
            f"OpenAI Embedding API failed after {_MAX_RETRIES} retries",
            code="EMBED_API_DEGRADED",
            details={"last_error": str(last_exc)},
        )

    def get_dimension(self) -> int:
        return _API_DIMENSION


class EmbeddingFactory:
    __test__ = False

    @staticmethod
    def create(settings: TestAgentSettings) -> IEmbeddingService:
        mode = settings.embedding_mode.lower()

        if mode == "local":
            try:
                return LocalEmbeddingService(model_name=settings.embedding_model)
            except RAGDegradedError:
                raise
            except Exception as exc:
                raise RAGDegradedError(
                    f"Failed to create local embedding service: {exc}",
                    code="LOCAL_EMBED_CREATE_FAILED",
                    details={"mode": mode, "error": str(exc)},
                ) from exc

        if mode == "openai":
            api_key = settings.openai_api_key.get_secret_value()
            from testagent.common.security import KeyManager

            if not api_key:
                try:
                    api_key = KeyManager.get_key("openai", "api_key")
                except Exception as exc:
                    raise RAGDegradedError(
                        "OpenAI API key not found; Embedding service degraded to BM25",
                        code="EMBED_API_KEY_MISSING",
                        details={"error": str(exc)},
                    ) from exc

            return APIEmbeddingService(
                api_key=api_key,
                model=settings.openai_embedding_model,
            )

        raise RAGDegradedError(
            f"Unknown embedding mode: {mode!r}",
            code="UNKNOWN_EMBEDDING_MODE",
            details={"mode": mode, "available": ["local", "openai"]},
        )
