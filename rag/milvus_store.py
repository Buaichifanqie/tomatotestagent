from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from testagent.common.errors import RAGSearchError
from testagent.common.logging import get_logger

logger = get_logger(__name__)

_DEFAULT_COLLECTION = "default"
_DEFAULT_TOP_K = 10
_DEFAULT_DIMENSION = 1024
_DEFAULT_INDEX_TYPE = "IVF_FLAT"
_DEFAULT_METRIC_TYPE = "COSINE"
_DEFAULT_NLIST = 128
_DEFAULT_M = 16
_DEFAULT_EF_CONSTRUCTION = 256
_DEFAULT_EF = 64


class MilvusVectorStore:
    __test__ = False

    def __init__(
        self,
        host: str = "localhost",
        port: int = 19530,
        collection_prefix: str = "testagent_",
        index_type: str = _DEFAULT_INDEX_TYPE,
        metric_type: str = _DEFAULT_METRIC_TYPE,
    ) -> None:
        self._host = host
        self._port = port
        self._collection_prefix = collection_prefix
        self._index_type = index_type
        self._metric_type = metric_type
        self._client: Any = None
        self._collections: dict[str, Any] = {}
        self._init_client()

    def _init_client(self) -> None:
        try:
            from pymilvus import MilvusClient  # type: ignore[import-not-found]

            self._client = MilvusClient(
                uri=f"http://{self._host}:{self._port}",
            )
            logger.info(
                "Connected to Milvus at %s:%d",
                self._host,
                self._port,
            )
        except ImportError:
            raise RAGSearchError(
                "pymilvus is not installed; install with: pip install pymilvus",
                code="PYMILVUS_NOT_INSTALLED",
                details={"host": self._host, "port": self._port},
            ) from None
        except Exception as exc:
            raise RAGSearchError(
                f"Failed to connect to Milvus at {self._host}:{self._port}: {exc}",
                code="MILVUS_CONNECTION_FAILED",
                details={"host": self._host, "port": self._port, "error": str(exc)},
            ) from exc

    def _collection_name(self, name: str) -> str:
        return f"{self._collection_prefix}{name}"

    def _get_or_create_collection(self, name: str, dimension: int) -> Any:
        full_name = self._collection_name(name)
        if full_name in self._collections:
            return self._collections[full_name]

        try:
            from pymilvus import Collection, CollectionSchema, DataType, FieldSchema

            if self._client.has_collection(full_name):
                collection = Collection(full_name, using=self._client._using)
            else:
                fields = [
                    FieldSchema(
                        name="id",
                        dtype=DataType.VARCHAR,
                        is_primary=True,
                        max_length=256,
                    ),
                    FieldSchema(
                        name="embedding",
                        dtype=DataType.FLOAT_VECTOR,
                        dim=dimension,
                    ),
                    FieldSchema(
                        name="document",
                        dtype=DataType.VARCHAR,
                        max_length=65535,
                    ),
                ]

                schema = CollectionSchema(
                    fields=fields,
                    enable_dynamic_field=True,
                )
                collection = Collection(
                    name=full_name,
                    schema=schema,
                    using=self._client._using if hasattr(self._client, "_using") else "default",
                )

                index_params: dict[str, Any] = {
                    "index_type": self._index_type,
                    "metric_type": self._metric_type,
                }
                if self._index_type == "IVF_FLAT":
                    index_params["params"] = {"nlist": _DEFAULT_NLIST}
                elif self._index_type == "HNSW":
                    index_params["params"] = {
                        "M": _DEFAULT_M,
                        "efConstruction": _DEFAULT_EF_CONSTRUCTION,
                    }

                collection.create_index(
                    field_name="embedding",
                    index_params=index_params,
                )
                collection.load()

                logger.info(
                    "Created Milvus collection '%s' (dim=%d, index=%s)",
                    full_name,
                    dimension,
                    self._index_type,
                )

            self._collections[full_name] = collection
            return collection

        except Exception as exc:
            raise RAGSearchError(
                f"Failed to get/create Milvus collection '{full_name}': {exc}",
                code="MILVUS_COLLECTION_ERROR",
                details={"collection": full_name, "dimension": dimension, "error": str(exc)},
            ) from exc

    async def create_collection(self, name: str, dimension: int) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._get_or_create_collection,
            name,
            dimension,
        )

    async def upsert(self, docs: list[dict[str, Any]]) -> None:
        if not docs:
            logger.warning("upsert called with empty docs list")
            return

        first_embedding = docs[0].get("embedding", [])
        dimension = len(first_embedding) if first_embedding else _DEFAULT_DIMENSION

        ids: list[str] = []
        embeddings: list[list[float]] = []
        metadatas: list[dict[str, Any]] = []
        documents: list[str] = []

        for doc in docs:
            doc_id = doc.get("id")
            if doc_id is None:
                logger.warning("Skipping doc without 'id' field")
                continue
            ids.append(str(doc_id))
            embeddings.append(doc.get("embedding", []))
            metadatas.append(doc.get("metadata", {}))
            documents.append(doc.get("document", ""))

        if not ids:
            return

        collection = self._get_or_create_collection(_DEFAULT_COLLECTION, dimension)
        full_name = self._collection_name(_DEFAULT_COLLECTION)

        loop = asyncio.get_running_loop()

        def _upsert_sync() -> None:
            data: list[dict[str, Any]] = []
            for i in range(len(ids)):
                row: dict[str, Any] = {
                    "id": ids[i],
                    "embedding": embeddings[i],
                    "document": documents[i],
                }
                row.update(metadatas[i])
                data.append(row)
            collection.upsert(data)

        try:
            await loop.run_in_executor(None, _upsert_sync)
            logger.info(
                "Upserted %d docs into Milvus collection '%s'",
                len(ids),
                full_name,
            )
        except Exception as exc:
            raise RAGSearchError(
                f"Milvus upsert failed: {exc}",
                code="MILVUS_UPSERT_ERROR",
                details={"collection": full_name, "doc_count": len(ids)},
            ) from exc

    async def search(
        self,
        query_vector: list[float],
        top_k: int = _DEFAULT_TOP_K,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        collection = self._get_or_create_collection(
            _DEFAULT_COLLECTION,
            len(query_vector),
        )
        full_name = self._collection_name(_DEFAULT_COLLECTION)

        loop = asyncio.get_running_loop()

        def _search_sync() -> list[dict[str, Any]]:
            search_params: dict[str, Any] = {}
            if self._index_type == "IVF_FLAT":
                search_params = {"metric_type": self._metric_type, "params": {"nprobe": 16}}
            elif self._index_type == "HNSW":
                search_params = {"metric_type": self._metric_type, "params": {"ef": _DEFAULT_EF}}

            expr: str | None = None
            if filters:
                conditions: list[str] = []
                for key, value in filters.items():
                    if isinstance(value, list):
                        quoted = ", ".join(f"'{v}'" if isinstance(v, str) else str(v) for v in value)
                        conditions.append(f"{key} in [{quoted}]")
                    elif isinstance(value, (bool, int, float)):
                        conditions.append(f"{key} == {value}")
                    elif isinstance(value, str):
                        conditions.append(f"{key} == '{value}'")
                if conditions:
                    expr = " and ".join(conditions)

            results = collection.search(
                data=[query_vector],
                anns_field="embedding",
                param=search_params,
                limit=top_k,
                expr=expr,
                output_fields=["document"],
            )

            formatted: list[dict[str, Any]] = []
            if results and len(results) > 0:
                for hit in results[0]:
                    metadata: dict[str, Any] = {}
                    if hasattr(hit, "entity") and hit.entity:
                        for field_name in hit.entity:
                            if field_name not in ("id", "embedding", "document"):
                                metadata[field_name] = hit.entity.get(field_name)
                    formatted.append(
                        {
                            "id": str(hit.id),
                            "score": float(hit.score),
                            "metadata": metadata,
                            "document": hit.entity.get("document", "") if hit.entity else "",
                        }
                    )
            return formatted

        try:
            return await loop.run_in_executor(None, _search_sync)
        except Exception as exc:
            raise RAGSearchError(
                f"Milvus search failed: {exc}",
                code="MILVUS_SEARCH_ERROR",
                details={"collection": full_name, "top_k": top_k},
            ) from exc

    async def delete(self, doc_ids: list[str]) -> None:
        if not doc_ids:
            logger.warning("delete called with empty doc_ids list")
            return

        collection = self._collections.get(
            self._collection_name(_DEFAULT_COLLECTION),
        )
        if collection is None:
            logger.warning("No collection loaded for delete operation")
            return

        full_name = self._collection_name(_DEFAULT_COLLECTION)
        loop = asyncio.get_running_loop()

        def _delete_sync() -> None:
            expr = f"id in [{', '.join(f'"{did}"' for did in doc_ids)}]"
            collection.delete(expr)

        try:
            await loop.run_in_executor(None, _delete_sync)
            logger.info(
                "Deleted %d docs from Milvus collection '%s'",
                len(doc_ids),
                full_name,
            )
        except Exception as exc:
            raise RAGSearchError(
                f"Milvus delete failed: {exc}",
                code="MILVUS_DELETE_ERROR",
                details={"collection": full_name, "doc_ids": doc_ids},
            ) from exc

    async def health_check(self) -> bool:
        loop = asyncio.get_running_loop()

        def _health_sync() -> bool:
            try:
                from pymilvus import utility

                utility.get_server_version()
                return True
            except Exception:
                return False

        try:
            return await loop.run_in_executor(None, _health_sync)
        except Exception:
            return False

    async def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):
                self._client.close()
            self._client = None
            self._collections.clear()
