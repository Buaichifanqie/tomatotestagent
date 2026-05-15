from __future__ import annotations

import sys
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from testagent.common.errors import RAGSearchError
from testagent.config.settings import TestAgentSettings
from testagent.rag.vector_store_factory import VectorStoreFactory


def _make_mock_milvus_client() -> MagicMock:
    mock_client = MagicMock()
    mock_client.has_collection.return_value = False
    mock_client._using = "default"
    return mock_client


def _make_mock_collection() -> MagicMock:
    mock_col = MagicMock()
    mock_col.search.return_value = []
    mock_col.upsert.return_value = None
    mock_col.delete.return_value = None
    return mock_col


def _build_fake_pymilvus() -> ModuleType:
    fake_module = ModuleType("pymilvus")
    fake_milvus_client = MagicMock
    fake_collection = MagicMock
    fake_collection_schema = MagicMock
    fake_field_schema = MagicMock
    fake_data_type = MagicMock()
    fake_data_type.VARCHAR = "VARCHAR"
    fake_data_type.FLOAT_VECTOR = "FLOAT_VECTOR"
    fake_data_type.BOOL = "BOOL"
    fake_data_type.INT64 = "INT64"
    fake_data_type.DOUBLE = "DOUBLE"
    fake_utility = MagicMock()
    fake_utility.get_server_version.return_value = "2.3.0"
    fake_module.MilvusClient = fake_milvus_client
    fake_module.Collection = fake_collection
    fake_module.CollectionSchema = fake_collection_schema
    fake_module.FieldSchema = fake_field_schema
    fake_module.DataType = fake_data_type
    fake_module.utility = fake_utility
    return fake_module


@pytest.fixture()
def _inject_pymilvus() -> Any:
    fake_pymilvus = _build_fake_pymilvus()
    with patch.dict(sys.modules, {"pymilvus": fake_pymilvus}):
        yield fake_pymilvus


@pytest.fixture()
def mock_client() -> MagicMock:
    return _make_mock_milvus_client()


@pytest.fixture()
def mock_collection() -> MagicMock:
    return _make_mock_collection()


@pytest.fixture()
def milvus_store(_inject_pymilvus: Any, mock_client: MagicMock) -> Any:
    from testagent.rag.milvus_store import MilvusVectorStore

    with patch.object(MilvusVectorStore, "_init_client"):
        store = MilvusVectorStore(host="localhost", port=19530)
        store._client = mock_client
        full_name = store._collection_name("default")
        store._collections.add(full_name)
        return store


class TestMilvusVectorStoreUpsert:
    async def test_upsert_basic(self, milvus_store: Any, mock_client: MagicMock) -> None:
        docs = [
            {
                "id": "doc_1",
                "embedding": [0.1, 0.2, 0.3],
                "metadata": {"source": "test", "collection": "api_docs"},
                "document": "test doc one",
            },
            {
                "id": "doc_2",
                "embedding": [0.4, 0.5, 0.6],
                "metadata": {"source": "prod", "collection": "req_docs"},
                "document": "test doc two",
            },
        ]
        await milvus_store.upsert(docs)
        mock_client.upsert.assert_called_once()
        call_args = mock_client.upsert.call_args.kwargs["data"]
        assert len(call_args) == 2
        assert call_args[0]["id"] == "doc_1"
        assert call_args[0]["embedding"] == [0.1, 0.2, 0.3]
        assert call_args[0]["document"] == "test doc one"
        assert call_args[0]["source"] == "test"
        assert call_args[0]["collection"] == "api_docs"

    async def test_upsert_empty_list(self, milvus_store: Any, mock_client: MagicMock) -> None:
        await milvus_store.upsert([])
        mock_client.upsert.assert_not_called()

    async def test_upsert_skips_doc_without_id(self, milvus_store: Any, mock_client: MagicMock) -> None:
        docs = [
            {"embedding": [0.1], "metadata": {}, "document": "no id"},
            {"id": "doc_ok", "embedding": [0.2], "metadata": {}, "document": "has id"},
        ]
        await milvus_store.upsert(docs)
        call_args = mock_client.upsert.call_args.kwargs["data"]
        assert len(call_args) == 1
        assert call_args[0]["id"] == "doc_ok"

    async def test_upsert_wraps_error(self, milvus_store: Any, mock_client: MagicMock) -> None:
        mock_client.upsert.side_effect = RuntimeError("milvus error")
        docs = [{"id": "x", "embedding": [0.1], "metadata": {}, "document": "d"}]
        with pytest.raises(RAGSearchError, match="MILVUS_UPSERT_ERROR"):
            await milvus_store.upsert(docs)


class TestMilvusVectorStoreSearch:
    async def test_search_basic(self, milvus_store: Any, mock_client: MagicMock) -> None:
        mock_client.search.return_value = [
            [{"id": "doc_1", "distance": 0.95, "entity": {"document": "found doc", "source": "api_docs"}}]
        ]

        results = await milvus_store.search(
            query_vector=[0.1, 0.2, 0.3],
            top_k=5,
        )
        assert len(results) == 1
        assert results[0]["id"] == "doc_1"
        assert results[0]["score"] == 0.95
        assert results[0]["document"] == "found doc"
        assert results[0]["metadata"]["source"] == "api_docs"

    async def test_search_with_metadata_filters(self, milvus_store: Any, mock_client: MagicMock) -> None:
        mock_client.search.return_value = []

        await milvus_store.search(
            query_vector=[0.1, 0.2, 0.3],
            top_k=10,
            filters={"source": "alpha", "env": "staging"},
        )

        search_call = mock_client.search.call_args
        expr = search_call.kwargs.get("filter")
        assert expr is not None
        assert "source == 'alpha'" in expr
        assert "env == 'staging'" in expr
        assert " and " in expr

    async def test_search_with_list_filter(self, milvus_store: Any, mock_client: MagicMock) -> None:
        mock_client.search.return_value = []

        await milvus_store.search(
            query_vector=[0.1, 0.2, 0.3],
            top_k=5,
            filters={"source": ["alpha", "beta"]},
        )

        search_call = mock_client.search.call_args
        expr = search_call.kwargs.get("filter")
        assert expr is not None
        assert "source in [" in expr

    async def test_search_with_int_filter(self, milvus_store: Any, mock_client: MagicMock) -> None:
        mock_client.search.return_value = []

        await milvus_store.search(
            query_vector=[0.1, 0.2, 0.3],
            top_k=5,
            filters={"year": 2024},
        )

        search_call = mock_client.search.call_args
        expr = search_call.kwargs.get("filter")
        assert "year == 2024" in expr

    async def test_search_empty_results(self, milvus_store: Any, mock_client: MagicMock) -> None:
        mock_client.search.return_value = []
        results = await milvus_store.search(query_vector=[0.1, 0.2, 0.3], top_k=5)
        assert results == []

    async def test_search_wraps_error(self, milvus_store: Any, mock_client: MagicMock) -> None:
        mock_client.search.side_effect = RuntimeError("search failed")
        with pytest.raises(RAGSearchError, match="MILVUS_SEARCH_ERROR"):
            await milvus_store.search(query_vector=[0.1, 0.2, 0.3], top_k=5)


class TestMilvusVectorStoreDelete:
    async def test_delete_basic(self, milvus_store: Any, mock_client: MagicMock) -> None:
        await milvus_store.delete(["doc_1", "doc_2"])
        mock_client.delete.assert_called_once()
        call_args = mock_client.delete.call_args.kwargs["filter"]
        assert "doc_1" in call_args
        assert "doc_2" in call_args

    async def test_delete_empty_list(self, milvus_store: Any, mock_client: MagicMock) -> None:
        await milvus_store.delete([])
        mock_client.delete.assert_not_called()

    async def test_delete_no_collection_loaded(self, milvus_store: Any) -> None:
        milvus_store._collections.clear()
        await milvus_store.delete(["doc_1"])

    async def test_delete_wraps_error(self, milvus_store: Any, mock_client: MagicMock) -> None:
        mock_client.delete.side_effect = RuntimeError("delete error")
        with pytest.raises(RAGSearchError, match="MILVUS_DELETE_ERROR"):
            await milvus_store.delete(["doc_1"])


class TestMilvusVectorStoreHealthCheck:
    async def test_health_check_success(self, milvus_store: Any, _inject_pymilvus: Any) -> None:
        result = await milvus_store.health_check()
        assert result is True

    async def test_health_check_failure(self, milvus_store: Any, mock_client: MagicMock) -> None:
        mock_client.list_collections.side_effect = Exception("connection refused")
        result = await milvus_store.health_check()
        assert result is False


class TestMilvusVectorStoreCreateCollection:
    async def test_create_collection_delegates(self, milvus_store: Any, mock_client: MagicMock) -> None:
        called_with: dict[str, Any] = {}

        original = milvus_store._ensure_collection

        def _fake_ensure(name: str, dimension: int) -> None:
            called_with["name"] = name
            called_with["dimension"] = dimension
            original(name, dimension)

        milvus_store._ensure_collection = _fake_ensure
        await milvus_store.create_collection("test_col", dimension=1024)

        assert called_with["name"] == "test_col"
        assert called_with["dimension"] == 1024


class TestMilvusVectorStoreClose:
    async def test_close_clears_state(self, milvus_store: Any, mock_client: MagicMock) -> None:
        assert milvus_store._client is not None
        assert len(milvus_store._collections) > 0
        await milvus_store.close()
        assert milvus_store._client is None
        assert len(milvus_store._collections) == 0
        mock_client.close.assert_called_once()


class TestVectorStoreFactory:
    def test_create_chromadb(self, tmp_path: Any) -> None:
        settings = TestAgentSettings(
            vector_store_backend="chromadb",
            chroma_persist_dir=str(tmp_path / "chroma"),
        )
        with patch("testagent.rag.vector_store_factory.ChromaDBVectorStore") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            store = VectorStoreFactory.create(settings)
            mock_cls.assert_called_once_with(persist_dir=str(tmp_path / "chroma"))
            assert store is mock_instance

    def test_create_chromadb_case_insensitive(self, tmp_path: Any) -> None:
        settings = TestAgentSettings(
            vector_store_backend="ChromaDB",
            chroma_persist_dir=str(tmp_path / "chroma"),
        )
        with patch("testagent.rag.vector_store_factory.ChromaDBVectorStore") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            store = VectorStoreFactory.create(settings)
            mock_cls.assert_called_once_with(persist_dir=str(tmp_path / "chroma"))
            assert store is mock_instance

    def test_create_milvus(self, _inject_pymilvus: Any) -> None:
        settings = TestAgentSettings(
            vector_store_backend="milvus",
            milvus_host="localhost",
            milvus_port=19530,
        )
        with patch("testagent.rag.milvus_store.MilvusVectorStore") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            store = VectorStoreFactory.create(settings)
            mock_cls.assert_called_once_with(
                host="localhost",
                port=19530,
                collection_prefix="testagent_",
            )
            assert store is mock_instance

    def test_create_unknown_backend_raises(self) -> None:
        settings = TestAgentSettings(
            vector_store_backend="weaviate",
        )
        with pytest.raises(RAGSearchError, match="UNKNOWN_VECTOR_STORE_BACKEND"):
            VectorStoreFactory.create(settings)

    def test_create_milvus_passes_settings(self, _inject_pymilvus: Any) -> None:
        settings = TestAgentSettings(
            vector_store_backend="milvus",
            milvus_host="milvus-server",
            milvus_port=19531,
            milvus_collection_prefix="custom_",
        )
        with patch("testagent.rag.milvus_store.MilvusVectorStore") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            store = VectorStoreFactory.create(settings)
            mock_cls.assert_called_once_with(
                host="milvus-server",
                port=19531,
                collection_prefix="custom_",
            )
            assert store is mock_instance

    def test_create_milvus_init_error_raises(self, _inject_pymilvus: Any) -> None:
        settings = TestAgentSettings(
            vector_store_backend="milvus",
            milvus_host="localhost",
            milvus_port=19530,
        )
        with patch("testagent.rag.milvus_store.MilvusVectorStore") as mock_cls:
            mock_cls.side_effect = RuntimeError("connection failed")
            with pytest.raises(RAGSearchError, match="MILVUS_CREATE_FAILED"):
                VectorStoreFactory.create(settings)


class TestMilvusVectorStoreInit:
    def test_collection_name_prefix(self, _inject_pymilvus: Any) -> None:
        from testagent.rag.milvus_store import MilvusVectorStore

        with patch.object(MilvusVectorStore, "_init_client"):
            store = MilvusVectorStore(
                host="localhost",
                port=19530,
                collection_prefix="testagent_",
            )
            assert store._collection_name("api_docs") == "testagent_api_docs"

    def test_init_raises_on_pymilvus_import_error(self, _inject_pymilvus: Any) -> None:
        from testagent.rag.milvus_store import MilvusVectorStore

        def _failing_init(self: Any) -> None:
            raise RAGSearchError(
                "pymilvus is not installed; install with: pip install pymilvus",
                code="PYMILVUS_NOT_INSTALLED",
            )

        with (
            patch.object(MilvusVectorStore, "_init_client", _failing_init),
            pytest.raises(RAGSearchError, match="PYMILVUS_NOT_INSTALLED"),
        ):
            MilvusVectorStore(host="localhost", port=19530)

    def test_init_stores_host_port(self, _inject_pymilvus: Any) -> None:
        from testagent.rag.milvus_store import MilvusVectorStore

        with patch.object(MilvusVectorStore, "_init_client"):
            store = MilvusVectorStore(
                host="my-host",
                port=19531,
                collection_prefix="prefix_",
                index_type="HNSW",
                metric_type="L2",
            )
            assert store._host == "my-host"
            assert store._port == 19531
            assert store._collection_prefix == "prefix_"
            assert store._index_type == "HNSW"
            assert store._metric_type == "L2"
