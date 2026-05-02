from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from meilisearch_python_async import Client as MeiliClient

from testagent.common.errors import RAGSearchError
from testagent.common.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_INDEX = "default"
_DEFAULT_TOP_K = 10


@runtime_checkable
class IFullTextSearch(Protocol):
    async def index(self, docs: list[dict[str, Any]]) -> None: ...
    async def search(
        self,
        query: str,
        top_k: int = _DEFAULT_TOP_K,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]: ...
    async def delete(self, doc_ids: list[str]) -> None: ...


class MeilisearchFullText:
    __test__ = False

    def __init__(self, url: str, api_key: str, index_name: str = _DEFAULT_INDEX) -> None:
        self._url = url
        self._api_key = api_key
        self._index_name = index_name
        self._client: MeiliClient | None = None

    async def _get_client(self) -> MeiliClient:
        if self._client is None:
            self._client = MeiliClient(self._url, self._api_key)
        return self._client

    async def _ensure_index(self) -> None:
        client = await self._get_client()
        try:
            await client.get_index(self._index_name)
        except Exception:
            await client.create_index(self._index_name, primary_key="id")

    @staticmethod
    def _build_filter_string(filters: dict[str, Any]) -> str:
        clauses: list[str] = []
        for key, value in filters.items():
            if isinstance(value, bool):
                clauses.append(f"{key} = {'true' if value else 'false'}")
            elif isinstance(value, str):
                escaped = value.replace("'", "\\'")
                clauses.append(f"{key} = '{escaped}'")
            elif isinstance(value, (int, float)):
                clauses.append(f"{key} = {value}")
            elif isinstance(value, list):
                parts: list[str] = []
                for item in value:
                    if isinstance(item, str):
                        escaped_item = item.replace("'", "\\'")
                        parts.append(f"{key} = '{escaped_item}'")
                    else:
                        parts.append(f"{key} = {item}")
                if len(parts) == 1:
                    clauses.append(parts[0])
                else:
                    clauses.append(f"({' OR '.join(parts)})")
        return " AND ".join(clauses)

    async def index(self, docs: list[dict[str, Any]]) -> None:
        if not docs:
            logger.warning("index called with empty docs list")
            return

        await self._ensure_index()
        client = await self._get_client()
        index = client.index(self._index_name)

        payload: list[dict[str, Any]] = []
        for doc in docs:
            entry: dict[str, Any] = {
                "id": doc.get("id", ""),
            }
            if "document" in doc:
                entry["content"] = doc["document"]
            entry.update(doc.get("metadata", {}))
            payload.append(entry)

        try:
            result = await index.add_documents(payload)
            logger.info(
                "Indexed %d docs into Meilisearch index '%s' (task=%s)",
                len(payload),
                self._index_name,
                result.task_uid,
            )
        except Exception as exc:
            raise RAGSearchError(
                f"Meilisearch index failed: {exc}",
                code="MEILI_INDEX_ERROR",
                details={"index": self._index_name, "doc_count": len(payload)},
            ) from exc

    async def search(
        self,
        query: str,
        top_k: int = _DEFAULT_TOP_K,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        client = await self._get_client()
        index = client.index(self._index_name)

        filter_str: str | None = None
        if filters is not None and filters:
            filter_str = self._build_filter_string(filters)

        try:
            result = await index.search(query, limit=top_k, filter=filter_str)
        except Exception as exc:
            raise RAGSearchError(
                f"Meilisearch search failed: {exc}",
                code="MEILI_SEARCH_ERROR",
                details={"index": self._index_name, "query": query, "top_k": top_k},
            ) from exc

        return self._format_results(result)

    @staticmethod
    def _format_results(raw: Any) -> list[dict[str, Any]]:
        formatted: list[dict[str, Any]] = []
        for hit in raw.hits:
            metadata = {k: v for k, v in hit.items() if k not in {"id", "content", "_formatted"}}
            formatted.append(
                {
                    "id": hit.get("id", ""),
                    "score": hit.get("_score", 0.0),
                    "metadata": metadata,
                    "document": hit.get("content", ""),
                }
            )
        return formatted

    async def delete(self, doc_ids: list[str]) -> None:
        if not doc_ids:
            logger.warning("delete called with empty doc_ids list")
            return

        await self._ensure_index()
        client = await self._get_client()
        index = client.index(self._index_name)

        try:
            result = await index.delete_documents(doc_ids)
            logger.info(
                "Deleted %d docs from Meilisearch index '%s' (task=%s)",
                len(doc_ids),
                self._index_name,
                result.task_uid,
            )
        except Exception as exc:
            raise RAGSearchError(
                f"Meilisearch delete failed: {exc}",
                code="MEILI_DELETE_ERROR",
                details={"index": self._index_name, "doc_ids": doc_ids},
            ) from exc
