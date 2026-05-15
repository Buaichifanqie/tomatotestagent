from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from testagent.rag.fulltext import MeilisearchFullText


@dataclass
class MockTask:
    task_uid: int


@dataclass
class MockHit:
    hits: list[dict[str, Any]]


@pytest.fixture
def fulltext() -> MeilisearchFullText:
    return MeilisearchFullText(
        url="http://localhost:7700",
        api_key="test_key",
        index_name="test_index",
    )


def _make_client() -> tuple[MagicMock, AsyncMock]:
    client = MagicMock()
    index = AsyncMock()
    index.add_documents.return_value = MockTask(task_uid=42)
    index.search.return_value = MockHit(hits=[])
    index.delete_documents.return_value = MockTask(task_uid=99)
    client.index.return_value = index
    client.get_index = AsyncMock(side_effect=Exception("index not found"))
    client.create_index = AsyncMock()
    return client, index


class TestMeilisearchFullText:
    async def test_index(self, fulltext: MeilisearchFullText) -> None:
        client, index = _make_client()
        index.search.return_value = MockHit(
            hits=[
                {"id": "doc_1", "content": "hello world", "source": "test", "priority": 1, "_score": 0.95},
            ]
        )
        fulltext._client = client

        await fulltext.index(
            [
                {
                    "id": "doc_1",
                    "document": "hello world",
                    "metadata": {"source": "test", "priority": 1},
                },
                {
                    "id": "doc_2",
                    "document": "goodbye world",
                    "metadata": {"source": "prod", "priority": 2},
                },
            ]
        )

        client.create_index.assert_awaited_once_with("test_index", primary_key="id")
        index.add_documents.assert_awaited_once()
        call_args = index.add_documents.call_args[0][0]
        assert len(call_args) == 2
        assert call_args[0]["id"] == "doc_1"
        assert call_args[0]["content"] == "hello world"
        assert call_args[0]["source"] == "test"
        assert call_args[0]["priority"] == 1

    async def test_search(self, fulltext: MeilisearchFullText) -> None:
        client, index = _make_client()
        index.search.return_value = MockHit(
            hits=[
                {
                    "id": "doc_1",
                    "content": "hello world",
                    "source": "test",
                    "priority": 1,
                    "_score": 0.95,
                },
            ]
        )
        fulltext._client = client

        results = await fulltext.search(query="hello", top_k=5)

        assert len(results) == 1
        r = results[0]
        assert r["id"] == "doc_1"
        assert r["score"] == 0.95
        assert r["document"] == "hello world"
        assert r["metadata"]["source"] == "test"
        assert r["metadata"]["priority"] == 1

    async def test_search_with_filters(self, fulltext: MeilisearchFullText) -> None:
        client, index = _make_client()
        fulltext._client = client

        await fulltext.search(
            query="test",
            top_k=10,
            filters={"source": "staging", "env": "production"},
        )

        _args, kwargs = index.search.call_args
        assert "filter" in kwargs
        filter_str = kwargs["filter"]
        assert filter_str is not None
        assert "source" in filter_str
        assert "env" in filter_str

    async def test_search_empty_results(self, fulltext: MeilisearchFullText) -> None:
        client, _ = _make_client()
        fulltext._client = client

        results = await fulltext.search(query="nonexistent", top_k=5)

        assert results == []

    async def test_delete(self, fulltext: MeilisearchFullText) -> None:
        client, index = _make_client()
        fulltext._client = client

        await fulltext.delete(["doc_1", "doc_2"])

        index.delete_documents.assert_awaited_once_with(["doc_1", "doc_2"])

    async def test_index_empty_list(self, fulltext: MeilisearchFullText) -> None:
        client, index = _make_client()
        fulltext._client = client

        await fulltext.index([])

        index.add_documents.assert_not_called()

    async def test_delete_empty_list(self, fulltext: MeilisearchFullText) -> None:
        client, index = _make_client()
        fulltext._client = client

        await fulltext.delete([])

        index.delete_documents.assert_not_called()

    async def test_search_result_fields(self, fulltext: MeilisearchFullText) -> None:
        client, index = _make_client()
        index.search.return_value = MockHit(
            hits=[
                {
                    "id": "field_test",
                    "content": "some content",
                    "extra_field": "extra",
                    "_score": 0.8,
                },
            ]
        )
        fulltext._client = client

        results = await fulltext.search(query="content", top_k=5)

        assert len(results) == 1
        r = results[0]
        assert set(r.keys()) == {"id", "score", "metadata", "document"}
        assert r["id"] == "field_test"
        assert r["score"] == 0.8
        assert r["document"] == "some content"
        assert r["metadata"]["extra_field"] == "extra"

    async def test_filter_string_building(self) -> None:
        ft = MeilisearchFullText(url="http://x:7700", api_key="x", index_name="x")

        result = ft._build_filter_string({"source": "test_doc"})
        assert result == "source = 'test_doc'"

        result = ft._build_filter_string({"priority": 1})
        assert result == "priority = 1"

        result = ft._build_filter_string({"active": True})
        assert result == "active = true"

        result = ft._build_filter_string({"tag": ["a", "b", "c"]})
        assert " OR " in result

        result = ft._build_filter_string({"a": 1, "b": "two"})
        assert " AND " in result
