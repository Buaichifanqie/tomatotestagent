from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from testagent.rag.vector_store import ChromaDBVectorStore


@pytest.fixture
def store(tmp_path: Path) -> ChromaDBVectorStore:
    persist_dir = tmp_path / "chroma_test"
    persist_dir.mkdir(exist_ok=True)
    return ChromaDBVectorStore(
        persist_dir=str(persist_dir),
        collection_name="test_collection",
    )


class TestChromaDBVectorStore:
    async def test_upsert_and_search(self, store: ChromaDBVectorStore) -> None:
        docs = [
            {
                "id": "doc_1",
                "embedding": [0.1, 0.2, 0.3, 0.4],
                "metadata": {"source": "test_a", "type": "api"},
                "document": "test document one",
            },
            {
                "id": "doc_2",
                "embedding": [0.5, 0.6, 0.7, 0.8],
                "metadata": {"source": "test_b", "type": "web"},
                "document": "test document two",
            },
        ]
        await store.upsert(docs)

        results = await store.search(
            query_vector=[0.1, 0.2, 0.3, 0.4],
            top_k=5,
        )

        assert len(results) >= 1
        first = results[0]
        assert first["id"] == "doc_1"
        assert "score" in first
        assert first["metadata"]["source"] == "test_a"
        assert first["document"] == "test document one"

    async def test_search_with_metadata_filters(self, store: ChromaDBVectorStore) -> None:
        docs = [
            {
                "id": "filter_1",
                "embedding": [0.1, 0.0, 0.0, 0.0],
                "metadata": {"source": "alpha", "env": "staging"},
                "document": "alpha staging doc",
            },
            {
                "id": "filter_2",
                "embedding": [0.2, 0.0, 0.0, 0.0],
                "metadata": {"source": "beta", "env": "production"},
                "document": "beta production doc",
            },
            {
                "id": "filter_3",
                "embedding": [0.3, 0.0, 0.0, 0.0],
                "metadata": {"source": "alpha", "env": "production"},
                "document": "alpha production doc",
            },
        ]
        await store.upsert(docs)

        results = await store.search(
            query_vector=[0.1, 0.0, 0.0, 0.0],
            top_k=10,
            filters={"source": "alpha"},
        )

        assert len(results) == 2
        for r in results:
            assert r["metadata"]["source"] == "alpha"

    async def test_delete_removes_docs(self, store: ChromaDBVectorStore) -> None:
        docs = [
            {
                "id": "del_1",
                "embedding": [0.9, 0.0, 0.0, 0.0],
                "metadata": {"tag": "delete_me"},
                "document": "to be deleted",
            },
        ]
        await store.upsert(docs)
        before = await store.search(
            query_vector=[0.9, 0.0, 0.0, 0.0],
            top_k=10,
        )
        assert len(before) == 1

        await store.delete(["del_1"])
        after = await store.search(
            query_vector=[0.9, 0.0, 0.0, 0.0],
            top_k=10,
        )
        assert len(after) == 0

    async def test_search_empty_results(self, store: ChromaDBVectorStore) -> None:
        results = await store.search(
            query_vector=[0.0, 0.0, 0.0, 0.0],
            top_k=5,
        )
        assert results == []

    async def test_upsert_empty_list(self, store: ChromaDBVectorStore) -> None:
        await store.upsert([])
        results = await store.search(
            query_vector=[0.1, 0.2, 0.3, 0.4],
            top_k=5,
        )
        assert results == []

    async def test_delete_empty_list(self, store: ChromaDBVectorStore) -> None:
        await store.delete([])

    async def test_search_result_fields(self, store: ChromaDBVectorStore) -> None:
        docs = [
            {
                "id": "field_test",
                "embedding": [0.5, 0.5, 0.5, 0.5],
                "metadata": {"key": "value"},
                "document": "field check",
            },
        ]
        await store.upsert(docs)
        results = await store.search(
            query_vector=[0.5, 0.5, 0.5, 0.5],
            top_k=5,
        )
        assert len(results) == 1
        r = results[0]
        assert set(r.keys()) == {"id", "score", "metadata", "document"}
        assert isinstance(r["id"], str)
        assert isinstance(r["score"], float)
        assert isinstance(r["metadata"], dict)
        assert isinstance(r["document"], str)
