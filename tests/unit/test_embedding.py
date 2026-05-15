from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import SecretStr

from testagent.common.errors import RAGDegradedError, RAGError
from testagent.config.settings import TestAgentSettings
from testagent.rag.embedding import (
    APIEmbeddingService,
    EmbeddingFactory,
    EmbeddingFailover,
    IEmbeddingService,
    LocalEmbeddingService,
    SimpleEmbeddingService,
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


class TestIEmbeddingServiceProtocol:
    def test_protocol_is_runtime_checkable(self) -> None:
        assert hasattr(IEmbeddingService, "_is_runtime_protocol")

    def test_api_embedding_satisfies_protocol(self) -> None:
        svc = APIEmbeddingService(api_key="sk-test")
        assert isinstance(svc, IEmbeddingService)

    def test_local_embedding_satisfies_protocol(self) -> None:
        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            return_value=MagicMock(),
        ):
            svc = LocalEmbeddingService()
            assert isinstance(svc, IEmbeddingService)

    def test_custom_class_satisfies_protocol(self) -> None:
        class DummyEmbeddingService:
            async def embed(self, text: str) -> list[float]:
                return [0.0]

            async def embed_batch(self, texts: list[str]) -> list[list[float]]:
                return [[0.0]]

            def get_dimension(self) -> int:
                return 128

        assert isinstance(DummyEmbeddingService(), IEmbeddingService)

    def test_incomplete_class_does_not_satisfy_protocol(self) -> None:
        class IncompleteService:
            async def embed(self, text: str) -> list[float]:
                return [0.0]

        assert not isinstance(IncompleteService(), IEmbeddingService)


class TestLocalEmbeddingService:
    def _mock_model(self) -> MagicMock:
        model = MagicMock()
        model.encode = MagicMock()
        return model

    def test_init_loads_model(self) -> None:
        mock_model = self._mock_model()
        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            return_value=mock_model,
        ):
            svc = LocalEmbeddingService(model_name="BAAI/bge-large-zh-v1.5")
            assert svc._model_name == "BAAI/bge-large-zh-v1.5"
            assert svc._model is mock_model

    def test_init_default_model_name(self) -> None:
        mock_model = self._mock_model()
        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            return_value=mock_model,
        ):
            svc = LocalEmbeddingService()
            assert svc._model_name == "BAAI/bge-large-zh-v1.5"

    async def test_embed_single(self) -> None:
        mock_model = self._mock_model()
        embedding = [0.1] * 1024

        class MockEmbedding:
            def __init__(self, values: list[float]) -> None:
                self._values = values

            def tolist(self) -> list[float]:
                return self._values

        mock_model.encode.return_value = [MockEmbedding(embedding)]

        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            return_value=mock_model,
        ):
            svc = LocalEmbeddingService()
            result = await svc.embed("测试文本")

        assert len(result) == 1024
        assert result == embedding
        mock_model.encode.assert_called_once_with(["测试文本"])

    async def test_embed_batch(self) -> None:
        mock_model = self._mock_model()
        emb1 = [0.1] * 1024
        emb2 = [0.2] * 1024

        class MockEmbedding:
            def __init__(self, values: list[float]) -> None:
                self._values = values

            def tolist(self) -> list[float]:
                return self._values

        mock_model.encode.return_value = [MockEmbedding(emb1), MockEmbedding(emb2)]

        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            return_value=mock_model,
        ):
            svc = LocalEmbeddingService()
            result = await svc.embed_batch(["文本1", "文本2"])

        assert len(result) == 2
        assert len(result[0]) == 1024
        assert len(result[1]) == 1024
        assert result[0] == emb1
        assert result[1] == emb2
        mock_model.encode.assert_called_once_with(["文本1", "文本2"])

    def test_get_dimension(self) -> None:
        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            return_value=self._mock_model(),
        ):
            svc = LocalEmbeddingService()
            assert svc.get_dimension() == 1024

    def test_load_model_sentence_transformers_not_installed(self) -> None:
        with patch.dict("sys.modules", {"sentence_transformers": None}):
            with pytest.raises(RAGDegradedError) as exc_info:
                LocalEmbeddingService._load_model(LocalEmbeddingService(model_name="test"))
            assert exc_info.value.code == "SENTENCE_TRANSFORMERS_NOT_INSTALLED"

    def test_load_model_import_error(self) -> None:
        with patch.object(
            LocalEmbeddingService,
            "_load_model",
            side_effect=RAGDegradedError(
                "sentence-transformers is not installed",
                code="SENTENCE_TRANSFORMERS_NOT_INSTALLED",
            ),
        ):
            with pytest.raises(RAGDegradedError) as exc_info:
                LocalEmbeddingService(model_name="BAAI/bge-large-zh-v1.5")
            assert exc_info.value.code == "SENTENCE_TRANSFORMERS_NOT_INSTALLED"

    def test_load_model_runtime_error(self) -> None:
        with patch.object(
            LocalEmbeddingService,
            "_load_model",
            side_effect=RAGDegradedError(
                "Failed to load local embedding model",
                code="LOCAL_MODEL_LOAD_FAILED",
            ),
        ):
            with pytest.raises(RAGDegradedError) as exc_info:
                LocalEmbeddingService(model_name="BAAI/bge-large-zh-v1.5")
            assert exc_info.value.code == "LOCAL_MODEL_LOAD_FAILED"


class TestAPIEmbeddingService:
    def test_init(self) -> None:
        svc = APIEmbeddingService(api_key="sk-test-key", model="text-embedding-3-small")
        assert svc._key_rotator.key_count == 1
        assert svc._model == "text-embedding-3-small"

    def test_init_default_model(self) -> None:
        svc = APIEmbeddingService(api_key="sk-test-key")
        assert svc._model == "text-embedding-3-small"

    def test_init_with_multiple_keys(self) -> None:
        svc = APIEmbeddingService(api_keys=["key1", "key2", "key3"])
        assert svc._key_rotator.key_count == 3

    def test_init_with_no_keys_raises(self) -> None:
        with pytest.raises(ValueError, match="Either api_key or api_keys must be provided"):
            APIEmbeddingService()

    def test_get_dimension(self) -> None:
        svc = APIEmbeddingService(api_key="sk-test-key")
        assert svc.get_dimension() == 1536

    async def test_embed_single(self) -> None:
        svc = APIEmbeddingService(api_key="sk-test-key")
        embedding = [0.1] * 1536
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _make_embed_response([embedding])
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch.object(svc, "_get_client", return_value=mock_client):
            result = await svc.embed("test text")

        assert len(result) == 1536
        assert result == embedding

    async def test_embed_batch(self) -> None:
        svc = APIEmbeddingService(api_key="sk-test-key")
        emb1 = [0.1] * 1536
        emb2 = [0.2] * 1536
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _make_embed_response([emb1, emb2])
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False

        with patch.object(svc, "_get_client", return_value=mock_client):
            result = await svc.embed_batch(["text1", "text2"])

        assert len(result) == 2
        assert len(result[0]) == 1536
        assert len(result[1]) == 1536
        assert result[0] == emb1
        assert result[1] == emb2

    async def test_embed_api_error_non_retryable(self) -> None:
        svc = APIEmbeddingService(api_key="sk-test-key")
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "401",
                request=MagicMock(),
                response=mock_resp,
            )
        )
        mock_client.is_closed = False

        with patch.object(svc, "_get_client", return_value=mock_client):
            with pytest.raises(RAGError) as exc_info:
                await svc.embed("bad key test")
            assert exc_info.value.code == "EMBED_API_ERROR"

    async def test_embed_429_retry(self) -> None:
        svc = APIEmbeddingService(api_key="sk-test-key")

        mock_429 = MagicMock()
        mock_429.status_code = 429

        mock_success = MagicMock()
        mock_success.status_code = 200
        embedding = [0.5] * 1536
        mock_success.json.return_value = _make_embed_response([embedding])
        mock_success.raise_for_status = MagicMock()

        call_count = 0

        async def _mock_post(url: str, json: Any, headers: Any = None) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return mock_429
            return mock_success

        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_client.is_closed = False

        with patch.object(svc, "_get_client", return_value=mock_client):
            result = await svc.embed("retry test")

        assert len(result) == 1536
        assert call_count == 3

    async def test_embed_exhausts_retries(self) -> None:
        svc = APIEmbeddingService(api_key="sk-test-key")

        mock_503 = MagicMock()
        mock_503.status_code = 503

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_503)
        mock_client.is_closed = False

        with patch.object(svc, "_get_client", return_value=mock_client):
            with pytest.raises(RAGDegradedError) as exc_info:
                await svc.embed("exhaust retries")
            assert exc_info.value.code == "EMBED_API_DEGRADED"

    async def test_embed_timeout_retry(self) -> None:
        svc = APIEmbeddingService(api_key="sk-test-key")

        call_count = 0

        async def _mock_post(url: str, json: Any, headers: Any = None) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise httpx.TimeoutException("timeout")
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = _make_embed_response([[0.1] * 1536])
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_client.is_closed = False

        with patch.object(svc, "_get_client", return_value=mock_client):
            result = await svc.embed("timeout test")

        assert len(result) == 1536

    async def test_embed_connect_error_retry(self) -> None:
        svc = APIEmbeddingService(api_key="sk-test-key")

        call_count = 0

        async def _mock_post(url: str, json: Any, headers: Any = None) -> MagicMock:
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise httpx.ConnectError("Connection refused")
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = _make_embed_response([[0.1] * 1536])
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        mock_client = AsyncMock()
        mock_client.post = _mock_post
        mock_client.is_closed = False

        with patch.object(svc, "_get_client", return_value=mock_client):
            result = await svc.embed("connect test")

        assert len(result) == 1536

    async def test_close_client(self) -> None:
        svc = APIEmbeddingService(api_key="sk-test-key")
        mock_client = AsyncMock()
        mock_client.is_closed = False
        svc._client = mock_client
        await svc.close()
        mock_client.aclose.assert_awaited_once()
        assert svc._client is None


class TestEmbeddingFactory:
    def test_create_local(self) -> None:
        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            return_value=MagicMock(),
        ):
            settings = _make_settings(embedding_mode="local")
            svc = EmbeddingFactory.create(settings)
            assert isinstance(svc, EmbeddingFailover)
            assert isinstance(svc._primary, LocalEmbeddingService)
            assert svc._fallback is None

    def test_create_local_custom_model(self) -> None:
        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            return_value=MagicMock(),
        ):
            settings = _make_settings(
                embedding_mode="local",
                embedding_model="BAAI/bge-small-zh-v1.5",
            )
            svc = EmbeddingFactory.create(settings)
            assert isinstance(svc, EmbeddingFailover)
            assert isinstance(svc._primary, LocalEmbeddingService)
            assert svc._primary._model_name == "BAAI/bge-small-zh-v1.5"

    def test_create_openai(self) -> None:
        settings = _make_settings(
            embedding_mode="openai",
            openai_api_key=SecretStr("sk-openai-test-key"),
        )
        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            return_value=MagicMock(),
        ):
            svc = EmbeddingFactory.create(settings)
            assert isinstance(svc, EmbeddingFailover)
            assert isinstance(svc._primary, APIEmbeddingService)
            assert isinstance(svc._fallback, LocalEmbeddingService)

    def test_create_openai_custom_model(self) -> None:
        settings = _make_settings(
            embedding_mode="openai",
            openai_api_key=SecretStr("sk-openai-key"),
            openai_embedding_model="text-embedding-3-large",
        )
        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            return_value=MagicMock(),
        ):
            svc = EmbeddingFactory.create(settings)
            assert isinstance(svc, EmbeddingFailover)
            assert isinstance(svc._primary, APIEmbeddingService)
            assert svc._primary._model == "text-embedding-3-large"

    def test_create_openai_api_key_from_keyring(self) -> None:
        settings = _make_settings(
            embedding_mode="openai",
            openai_api_key=SecretStr(""),
        )
        with (
            patch(
                "testagent.common.security.KeyManager.get_key",
                return_value="sk-keyring-key",
            ),
            patch(
                "testagent.rag.embedding.LocalEmbeddingService._load_model",
                return_value=MagicMock(),
            ),
        ):
            svc = EmbeddingFactory.create(settings)
            assert isinstance(svc, EmbeddingFailover)
            assert isinstance(svc._primary, APIEmbeddingService)

    def test_create_openai_no_api_key_raises_degraded(self) -> None:
        settings = _make_settings(
            embedding_mode="openai",
            openai_api_key=SecretStr(""),
            llm_provider="openai",
        )
        with patch(
            "testagent.common.security.KeyManager.get_key",
            side_effect=Exception("No key found"),
        ):
            with pytest.raises(RAGDegradedError) as exc_info:
                EmbeddingFactory.create(settings)
            assert exc_info.value.code == "EMBED_API_KEY_MISSING"

    def test_create_unknown_mode_raises_degraded(self) -> None:
        settings = _make_settings(embedding_mode="unknown_mode")
        with pytest.raises(RAGDegradedError) as exc_info:
            EmbeddingFactory.create(settings)
        assert exc_info.value.code == "UNKNOWN_EMBEDDING_MODE"

    def test_create_case_insensitive(self) -> None:
        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            return_value=MagicMock(),
        ):
            settings = _make_settings(embedding_mode="LOCAL")
            svc = EmbeddingFactory.create(settings)
            assert isinstance(svc, EmbeddingFailover)
            assert isinstance(svc._primary, LocalEmbeddingService)

        settings = _make_settings(
            embedding_mode="OpenAI",
            openai_api_key=SecretStr("sk-case-test"),
        )
        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            return_value=MagicMock(),
        ):
            svc = EmbeddingFactory.create(settings)
            assert isinstance(svc, EmbeddingFailover)
            assert isinstance(svc._primary, APIEmbeddingService)

    def test_create_local_load_failure_falls_back_to_simple(self) -> None:
        settings = _make_settings(embedding_mode="local")
        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            side_effect=RAGDegradedError(
                "Model load failed",
                code="LOCAL_MODEL_LOAD_FAILED",
            ),
        ):
            svc = EmbeddingFactory.create(settings)
            assert isinstance(svc, EmbeddingFailover)
            assert isinstance(svc._primary, SimpleEmbeddingService)
            assert svc._primary.get_dimension() == 1024

    def test_create_local_unexpected_error_raises_degraded(self) -> None:
        settings = _make_settings(embedding_mode="local")
        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            side_effect=MemoryError("Out of memory"),
        ):
            with pytest.raises(RAGDegradedError) as exc_info:
                EmbeddingFactory.create(settings)
            assert exc_info.value.code == "LOCAL_EMBED_CREATE_FAILED"

    def test_create_service_implements_protocol(self) -> None:
        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            return_value=MagicMock(),
        ):
            local_svc = EmbeddingFactory.create(_make_settings(embedding_mode="local"))
            assert isinstance(local_svc, IEmbeddingService)

        with patch(
            "testagent.rag.embedding.LocalEmbeddingService._load_model",
            return_value=MagicMock(),
        ):
            api_svc = EmbeddingFactory.create(
                _make_settings(
                    embedding_mode="openai",
                    openai_api_key=SecretStr("sk-test"),
                )
            )
            assert isinstance(api_svc, IEmbeddingService)
