from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from testagent.common.errors import RAGDegradedError
from testagent.config.settings import TestAgentSettings
from testagent.rag.embedding import (
    APIEmbeddingService,
    EmbeddingFactory,
    EmbeddingFailover,
    IEmbeddingService,
    KeyRotator,
    LocalEmbeddingService,
)


def _make_settings(**overrides: Any) -> TestAgentSettings:
    defaults: dict[str, Any] = {
        "embedding_mode": "local",
        "embedding_model": "BAAI/bge-large-zh-v1.5",
        "openai_embedding_model": "text-embedding-3-small",
        "openai_api_key": SecretStr("sk-test-key-12345678"),
    }
    defaults.update(overrides)
    return TestAgentSettings(**defaults)


def _make_embed_response(embeddings: list[list[float]]) -> dict[str, Any]:
    return {
        "data": [{"embedding": emb, "index": i} for i, emb in enumerate(embeddings)],
        "model": "text-embedding-3-small",
        "usage": {"prompt_tokens": 5, "total_tokens": 5},
    }


class TestKeyRotator:
    def test_init_with_single_key(self) -> None:
        rotator = KeyRotator(["key1"])
        assert rotator.key_count == 1
        assert rotator.get_current_key() == "key1"

    def test_init_with_multiple_keys(self) -> None:
        rotator = KeyRotator(["key1", "key2", "key3"])
        assert rotator.key_count == 3

    def test_init_with_empty_keys_raises(self) -> None:
        with pytest.raises(ValueError, match="At least one API key is required"):
            KeyRotator([])

    async def test_get_next_key_rotates(self) -> None:
        rotator = KeyRotator(["key1", "key2", "key3"])

        key1 = await rotator.get_next_key()
        key2 = await rotator.get_next_key()
        key3 = await rotator.get_next_key()
        key4 = await rotator.get_next_key()

        assert key1 == "key1"
        assert key2 == "key2"
        assert key3 == "key3"
        assert key4 == "key1"

    async def test_get_next_key_concurrent(self) -> None:
        rotator = KeyRotator(["key1", "key2"])

        import asyncio

        tasks = [rotator.get_next_key() for _ in range(10)]
        results = await asyncio.gather(*tasks)

        assert len(set(results)) == 2
        assert "key1" in results
        assert "key2" in results


class TestAPIEmbeddingServiceWithKeyRotation:
    def test_init_with_single_key(self) -> None:
        svc = APIEmbeddingService(api_key="sk-test-key")
        assert svc._key_rotator.key_count == 1

    def test_init_with_multiple_keys(self) -> None:
        svc = APIEmbeddingService(api_keys=["key1", "key2", "key3"])
        assert svc._key_rotator.key_count == 3

    def test_init_with_no_keys_raises(self) -> None:
        with pytest.raises(ValueError, match="Either api_key or api_keys must be provided"):
            APIEmbeddingService()

    async def test_per_request_key_rotation(self) -> None:
        svc = APIEmbeddingService(api_keys=["key1", "key2"])

        used_keys: list[str] = []

        async def _mock_post(url: str, **kwargs: Any) -> MagicMock:
            auth_header = kwargs.get("headers", {}).get("Authorization", "")
            used_keys.append(auth_header.replace("Bearer ", ""))
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = _make_embed_response([[0.1] * 1536])
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_client.is_closed = False

        with patch.object(svc, "_get_client", return_value=mock_client):
            await svc.embed("test1")
            await svc.embed("test2")
            await svc.embed("test3")

        assert used_keys == ["key1", "key2", "key1"]

    async def test_429_retry_rotates_key(self) -> None:
        svc = APIEmbeddingService(api_keys=["key1", "key2"])

        call_count = 0
        used_keys: list[str] = []

        async def _mock_post(url: str, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            auth_header = kwargs.get("headers", {}).get("Authorization", "")
            used_keys.append(auth_header.replace("Bearer ", ""))
            if call_count <= 2:
                mock_resp = MagicMock()
                mock_resp.status_code = 429
                return mock_resp
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = _make_embed_response([[0.1] * 1536])
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_client.is_closed = False

        with patch.object(svc, "_get_client", return_value=mock_client):
            result = await svc.embed("test")

        assert len(result) == 1536
        assert call_count == 3
        assert used_keys == ["key1", "key2", "key1"]

    async def test_batch_request_splitting(self) -> None:
        svc = APIEmbeddingService(api_key="sk-test-key")

        call_count = 0

        async def _mock_post(url: str, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            batch_input = kwargs["json"]["input"]
            mock_resp.json.return_value = _make_embed_response([[0.1] * 1536] * len(batch_input))
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_client.is_closed = False

        with patch.object(svc, "_get_client", return_value=mock_client):
            texts = ["text"] * 150
            await svc.embed_batch(texts)

        assert call_count == 2

    async def test_token_limit_splitting(self) -> None:
        svc = APIEmbeddingService(api_key="sk-test-key")

        batches_sent: list[list[str]] = []

        async def _mock_post(url: str, **kwargs: Any) -> MagicMock:
            batches_sent.append(kwargs["json"]["input"])
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            batch_input = kwargs["json"]["input"]
            mock_resp.json.return_value = _make_embed_response([[0.1] * 1536] * len(batch_input))
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_client.is_closed = False

        with patch.object(svc, "_get_client", return_value=mock_client):
            long_text = "a" * 10000
            await svc.embed_batch([long_text])

        assert len(batches_sent) == 1
        assert len(batches_sent[0]) == 1

    async def test_429_retry_with_key_rotation(self) -> None:
        svc = APIEmbeddingService(api_keys=["key1", "key2"])

        call_count = 0

        async def _mock_post(url: str, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                mock_resp = MagicMock()
                mock_resp.status_code = 429
                return mock_resp
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = _make_embed_response([[0.1] * 1536])
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_client.is_closed = False

        with patch.object(svc, "_get_client", return_value=mock_client):
            result = await svc.embed("test")

        assert len(result) == 1536
        assert call_count == 3

    async def test_client_does_not_cache_auth_headers(self) -> None:
        svc = APIEmbeddingService(api_keys=["key-a", "key-b"])

        call_count = 0
        used_headers: list[dict[str, str]] = []

        async def _mock_post(url: str, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            used_headers.append(kwargs.get("headers", {}))
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = _make_embed_response([[0.1] * 1536])
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_client.is_closed = False

        client_create_count = 0

        async def _get_client() -> AsyncMock:
            nonlocal client_create_count
            client_create_count += 1
            return mock_client

        with patch.object(svc, "_get_client", side_effect=_get_client):
            await svc.embed("first")
            await svc.embed("second")

        assert client_create_count == 2
        assert used_headers[0].get("Authorization", "").endswith("key-a")
        assert used_headers[1].get("Authorization", "").endswith("key-b")


class TestEmbeddingFailover:
    def test_init(self) -> None:
        primary = MagicMock(spec=IEmbeddingService)
        fallback = MagicMock(spec=IEmbeddingService)

        failover = EmbeddingFailover(primary=primary, fallback=fallback)

        assert failover._primary is primary
        assert failover._fallback is fallback
        assert failover.primary_failures == 0
        assert failover.fallback_failures == 0
        assert failover.is_circuit_open is False
        assert failover.is_degraded is False

    def test_init_custom_threshold(self) -> None:
        primary = MagicMock(spec=IEmbeddingService)
        failover = EmbeddingFailover(primary=primary, fallback=None, circuit_breaker_threshold=5)
        assert failover._circuit_breaker_threshold == 5

    async def test_embed_primary_success(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed.return_value = [0.1] * 1536

        failover = EmbeddingFailover(primary=primary, fallback=None)

        result = await failover.embed("test")

        assert result == [0.1] * 1536
        primary.embed.assert_called_once_with("test")
        assert failover.primary_failures == 0
        assert failover.is_circuit_open is False

    async def test_embed_primary_failure_fallback_success(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed.side_effect = Exception("Primary failed")

        fallback = AsyncMock(spec=IEmbeddingService)
        fallback.embed.return_value = [0.2] * 1536

        failover = EmbeddingFailover(primary=primary, fallback=fallback)

        result = await failover.embed("test")

        assert result == [0.2] * 1536
        primary.embed.assert_called_once_with("test")
        fallback.embed.assert_called_once_with("test")
        assert failover.primary_failures == 1
        assert failover.fallback_failures == 0

    async def test_embed_both_fail_raises_degraded(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed.side_effect = Exception("Primary failed")

        fallback = AsyncMock(spec=IEmbeddingService)
        fallback.embed.side_effect = Exception("Fallback failed")

        failover = EmbeddingFailover(primary=primary, fallback=fallback)

        with pytest.raises(RAGDegradedError) as exc_info:
            await failover.embed("test")

        assert exc_info.value.code == "EMBED_SERVICE_DEGRADED"
        assert failover.primary_failures == 1
        assert failover.fallback_failures == 1

    async def test_embed_no_fallback_raises_degraded(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed.side_effect = Exception("Primary failed")

        failover = EmbeddingFailover(primary=primary, fallback=None)

        with pytest.raises(RAGDegradedError) as exc_info:
            await failover.embed("test")

        assert exc_info.value.code == "EMBED_SERVICE_DEGRADED"
        assert failover.primary_failures == 1

    async def test_embed_batch_primary_success(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed_batch.return_value = [[0.1] * 1536, [0.2] * 1536]

        failover = EmbeddingFailover(primary=primary, fallback=None)

        result = await failover.embed_batch(["text1", "text2"])

        assert result == [[0.1] * 1536, [0.2] * 1536]
        primary.embed_batch.assert_called_once_with(["text1", "text2"])

    async def test_embed_batch_primary_failure_fallback_success(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed_batch.side_effect = Exception("Primary batch failed")

        fallback = AsyncMock(spec=IEmbeddingService)
        fallback.embed_batch.return_value = [[0.3] * 1536, [0.4] * 1536]

        failover = EmbeddingFailover(primary=primary, fallback=fallback)

        result = await failover.embed_batch(["text1", "text2"])

        assert result == [[0.3] * 1536, [0.4] * 1536]
        assert failover.primary_failures == 1
        assert failover.fallback_failures == 0

    async def test_embed_batch_both_fail_raises_degraded(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed_batch.side_effect = Exception("Primary batch failed")

        fallback = AsyncMock(spec=IEmbeddingService)
        fallback.embed_batch.side_effect = Exception("Fallback batch failed")

        failover = EmbeddingFailover(primary=primary, fallback=fallback)

        with pytest.raises(RAGDegradedError) as exc_info:
            await failover.embed_batch(["text1", "text2"])

        assert exc_info.value.code == "EMBED_SERVICE_DEGRADED"
        assert failover.primary_failures == 1
        assert failover.fallback_failures == 1

    async def test_embed_batch_empty_texts(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)

        failover = EmbeddingFailover(primary=primary, fallback=None)

        result = await failover.embed_batch([])

        assert result == []
        primary.embed_batch.assert_not_called()

    def test_get_dimension(self) -> None:
        primary = MagicMock(spec=IEmbeddingService)
        primary.get_dimension.return_value = 1536

        failover = EmbeddingFailover(primary=primary, fallback=None)

        assert failover.get_dimension() == 1536

    async def test_failure_counter_resets_on_success(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed.side_effect = [Exception("fail1"), [0.1] * 1536]

        failover = EmbeddingFailover(primary=primary, fallback=None)

        with pytest.raises(RAGDegradedError):
            await failover.embed("test1")

        assert failover.primary_failures == 1

        result = await failover.embed("test2")

        assert result == [0.1] * 1536
        assert failover.primary_failures == 0

    async def test_reset_clears_counters_and_circuit(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed.side_effect = Exception("fail")

        failover = EmbeddingFailover(primary=primary, fallback=None, circuit_breaker_threshold=1)
        with pytest.raises(RAGDegradedError):
            await failover.embed("test")

        assert failover.primary_failures == 1
        assert failover.is_circuit_open is True

        failover.reset()
        assert failover.primary_failures == 0
        assert failover.fallback_failures == 0
        assert failover.is_circuit_open is False

    async def test_api_mode_normal_operation(self) -> None:
        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            return_value=MagicMock(),
        ):
            settings = _make_settings(
                embedding_mode="api",
                openai_api_key=SecretStr("sk-test-key"),
            )
            svc = EmbeddingFactory.create(settings)

            assert isinstance(svc, EmbeddingFailover)
            assert isinstance(svc._primary, APIEmbeddingService)
            assert isinstance(svc._fallback, LocalEmbeddingService)
            assert svc.is_circuit_open is False

    async def test_429_rate_limit_retry(self) -> None:
        svc = APIEmbeddingService(api_keys=["key1", "key2"])

        call_count = 0

        async def _mock_post(url: str, **kwargs: Any) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                mock_resp = MagicMock()
                mock_resp.status_code = 429
                return mock_resp
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = _make_embed_response([[0.1] * 1536])
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_client.is_closed = False

        with patch.object(svc, "_get_client", return_value=mock_client):
            result = await svc.embed("test")

        assert len(result) == 1536
        assert call_count == 3

    async def test_api_unavailable_degrades_to_local(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed.side_effect = RAGDegradedError(
            "API unavailable",
            code="EMBED_API_DEGRADED",
        )

        fallback = AsyncMock(spec=IEmbeddingService)
        fallback.embed.return_value = [0.1] * 1024

        failover = EmbeddingFailover(primary=primary, fallback=fallback)

        result = await failover.embed("test")

        assert result == [0.1] * 1024
        assert failover.primary_failures == 1
        assert failover.fallback_failures == 0

    async def test_both_unavailable_raises_rag_degraded(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed.side_effect = RAGDegradedError(
            "API unavailable",
            code="EMBED_API_DEGRADED",
        )

        fallback = AsyncMock(spec=IEmbeddingService)
        fallback.embed.side_effect = RAGDegradedError(
            "Local model unavailable",
            code="LOCAL_MODEL_LOAD_FAILED",
        )

        failover = EmbeddingFailover(primary=primary, fallback=fallback)

        with pytest.raises(RAGDegradedError) as exc_info:
            await failover.embed("test")

        assert exc_info.value.code == "EMBED_SERVICE_DEGRADED"
        assert "Embedding service unavailable, degraded to pure BM25" in str(exc_info.value.message)

    async def test_degraded_error_propagates_to_bm25(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed.side_effect = RuntimeError("API unreachable")

        fallback = AsyncMock(spec=IEmbeddingService)
        fallback.embed.side_effect = RuntimeError("Local model crashed")

        failover = EmbeddingFailover(primary=primary, fallback=fallback)

        with pytest.raises(RAGDegradedError) as exc_info:
            await failover.embed("test")

        assert exc_info.value.code == "EMBED_SERVICE_DEGRADED"
        assert "degraded to pure BM25" in str(exc_info.value.message)


class TestCircuitBreaker:
    async def test_circuit_opens_after_threshold_failures(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed.side_effect = Exception("fail")

        failover = EmbeddingFailover(
            primary=primary,
            fallback=None,
            circuit_breaker_threshold=2,
        )

        with pytest.raises(RAGDegradedError):
            await failover.embed("1")
        assert failover.is_circuit_open is False

        with pytest.raises(RAGDegradedError):
            await failover.embed("2")
        assert failover.is_circuit_open is True

    async def test_circuit_open_skips_primary_to_fallback(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed.side_effect = Exception("primary fail")

        fallback = AsyncMock(spec=IEmbeddingService)
        fallback.embed.side_effect = [
            Exception("fallback fail"),
            Exception("fallback fail"),
            [0.5] * 1024,
        ]

        failover = EmbeddingFailover(
            primary=primary,
            fallback=fallback,
            circuit_breaker_threshold=2,
        )

        with pytest.raises(RAGDegradedError):
            await failover.embed("1")
        assert failover.primary_failures == 1
        assert failover.is_circuit_open is False

        with pytest.raises(RAGDegradedError):
            await failover.embed("2")
        assert failover.primary_failures == 2
        assert failover.is_circuit_open is True

        assert primary.embed.call_count == 2

        result = await failover.embed("3")
        assert result == [0.5] * 1024
        assert primary.embed.call_count == 2
        fallback.embed.assert_called_with("3")

    async def test_circuit_closes_on_primary_success(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed.side_effect = [Exception("fail1"), [0.1] * 1536]

        failover = EmbeddingFailover(
            primary=primary,
            fallback=None,
            circuit_breaker_threshold=1,
        )

        with pytest.raises(RAGDegradedError):
            await failover.embed("1")
        assert failover.is_circuit_open is True

        result = await failover.embed("2")
        assert result == [0.1] * 1536
        assert failover.is_circuit_open is False

    async def test_circuit_breaker_batch(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed_batch.side_effect = Exception("primary fail")

        fallback = AsyncMock(spec=IEmbeddingService)
        fallback.embed_batch.side_effect = [
            Exception("fallback fail"),
            [[0.5] * 1024],
        ]

        failover = EmbeddingFailover(
            primary=primary,
            fallback=fallback,
            circuit_breaker_threshold=1,
        )

        with pytest.raises(RAGDegradedError):
            await failover.embed_batch(["1"])
        assert failover.primary_failures == 1
        assert failover.is_circuit_open is True
        assert primary.embed_batch.call_count == 1

        result = await failover.embed_batch(["2"])
        assert result == [[0.5] * 1024]
        assert primary.embed_batch.call_count == 1
        fallback.embed_batch.assert_called_with(["2"])

    async def test_circuit_open_fallback_also_fails(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed.side_effect = Exception("primary fail")

        fallback = AsyncMock(spec=IEmbeddingService)
        fallback.embed.side_effect = Exception("fallback fail")

        failover = EmbeddingFailover(
            primary=primary,
            fallback=fallback,
            circuit_breaker_threshold=1,
        )

        with pytest.raises(RAGDegradedError):
            await failover.embed("1")
        assert failover.is_circuit_open is True

        with pytest.raises(RAGDegradedError) as exc_info:
            await failover.embed("2")

        assert exc_info.value.code == "EMBED_SERVICE_DEGRADED"
        assert exc_info.value.details.get("circuit_open") is True
        assert primary.embed.call_count == 1
        assert fallback.embed.call_count == 2

    async def test_circuit_breaker_no_fallback_raised_degraded(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed.side_effect = Exception("fail")

        failover = EmbeddingFailover(
            primary=primary,
            fallback=None,
            circuit_breaker_threshold=1,
        )

        with pytest.raises(RAGDegradedError):
            await failover.embed("1")
        assert failover.is_circuit_open is True

        with pytest.raises(RAGDegradedError) as exc_info:
            await failover.embed("2")
        assert exc_info.value.code == "EMBED_SERVICE_DEGRADED"


class TestEmbeddingFactoryWithFailover:
    def test_create_local_mode(self) -> None:
        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            return_value=MagicMock(),
        ):
            settings = _make_settings(embedding_mode="local")
            svc = EmbeddingFactory.create(settings)

            assert isinstance(svc, EmbeddingFailover)
            assert isinstance(svc._primary, LocalEmbeddingService)
            assert svc._fallback is None

            assert svc.is_circuit_open is False

    def test_create_api_mode(self) -> None:
        settings = _make_settings(
            embedding_mode="api",
            openai_api_key=SecretStr("sk-test-key"),
        )

        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            return_value=MagicMock(),
        ):
            svc = EmbeddingFactory.create(settings)

            assert isinstance(svc, EmbeddingFailover)
            assert isinstance(svc._primary, APIEmbeddingService)
            assert isinstance(svc._fallback, LocalEmbeddingService)

    def test_create_api_mode_no_fallback(self) -> None:
        settings = _make_settings(
            embedding_mode="api",
            openai_api_key=SecretStr("sk-test-key"),
        )

        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            side_effect=Exception("Local model not available"),
        ):
            svc = EmbeddingFactory.create(settings)

            assert isinstance(svc, EmbeddingFailover)
            assert isinstance(svc._primary, APIEmbeddingService)
            assert svc._fallback is None

    def test_create_api_mode_no_keys_raises(self) -> None:
        settings = _make_settings(
            embedding_mode="api",
            openai_api_key=SecretStr(""),
        )

        with patch(
            "testagent.common.security.KeyManager.get_key",
            side_effect=Exception("No key found"),
        ):
            with pytest.raises(RAGDegradedError) as exc_info:
                EmbeddingFactory.create(settings)

            assert exc_info.value.code == "EMBED_API_KEY_MISSING"

    def test_create_api_mode_multiple_keys(self) -> None:
        import os

        settings = _make_settings(
            embedding_mode="api",
            openai_api_key=SecretStr("sk-key1"),
        )

        with (
            patch.dict(os.environ, {"TESTAGENT_OPENAI_API_KEYS": "sk-key2,sk-key3"}),
            patch(
                "testagent.rag.embedding.LocalEmbeddingService._load_model",
                return_value=MagicMock(),
            ),
        ):
            svc = EmbeddingFactory.create(settings)

            assert isinstance(svc, EmbeddingFailover)
            assert isinstance(svc._primary, APIEmbeddingService)
            assert svc._primary._key_rotator.key_count == 3


class TestEmbeddingServiceDegradation:
    async def test_api_unavailable_degrades_to_local(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed.side_effect = RAGDegradedError(
            "API unavailable",
            code="EMBED_API_DEGRADED",
        )

        fallback = AsyncMock(spec=IEmbeddingService)
        fallback.embed.return_value = [0.1] * 1024

        failover = EmbeddingFailover(primary=primary, fallback=fallback)

        result = await failover.embed("test")

        assert result == [0.1] * 1024
        assert failover.primary_failures == 1
        assert failover.fallback_failures == 0

    async def test_both_unavailable_raises_rag_degraded(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed.side_effect = RAGDegradedError(
            "API unavailable",
            code="EMBED_API_DEGRADED",
        )

        fallback = AsyncMock(spec=IEmbeddingService)
        fallback.embed.side_effect = RAGDegradedError(
            "Local model unavailable",
            code="LOCAL_MODEL_LOAD_FAILED",
        )

        failover = EmbeddingFailover(primary=primary, fallback=fallback)

        with pytest.raises(RAGDegradedError) as exc_info:
            await failover.embed("test")

        assert exc_info.value.code == "EMBED_SERVICE_DEGRADED"
        assert "Embedding service unavailable, degraded to pure BM25" in str(exc_info.value.message)

    async def test_rag_pipeline_uses_bm25_when_degraded(self) -> None:
        primary = AsyncMock(spec=IEmbeddingService)
        primary.embed.side_effect = RAGDegradedError(
            "API unavailable",
            code="EMBED_API_DEGRADED",
        )

        fallback = AsyncMock(spec=IEmbeddingService)
        fallback.embed.side_effect = RAGDegradedError(
            "Local model unavailable",
            code="LOCAL_MODEL_LOAD_FAILED",
        )

        failover = EmbeddingFailover(primary=primary, fallback=fallback)

        with pytest.raises(RAGDegradedError) as exc_info:
            await failover.embed("test")

        assert exc_info.value.code == "EMBED_SERVICE_DEGRADED"
        assert "degraded to pure BM25" in str(exc_info.value.message)
