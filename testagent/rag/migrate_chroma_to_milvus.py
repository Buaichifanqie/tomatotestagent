from __future__ import annotations

import asyncio
import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import chromadb

from testagent.common.errors import RAGError
from testagent.common.logging import get_logger
from testagent.rag.collections import RAG_COLLECTIONS
from testagent.rag.milvus_store import MilvusVectorStore

logger = get_logger(__name__)

_BATCH_SIZE = 1000
_VERIFY_SAMPLE_SIZE = 10
_CHECKPOINT_FILE = ".chroma_to_milvus_checkpoint.json"


class ChromaToMilvusMigrator:
    """ChromaDB to Milvus migration tool.

    Features:
      - Read full collection data from ChromaDB
      - Create corresponding Collections in Milvus
      - Batch write to Milvus (1000 per batch)
      - Verify migrated row count matches source
      - Sample verification for data consistency
      - Checkpoint-based resume support (JSON checkpoint)
    """

    def __init__(
        self,
        chroma_dir: str,
        milvus_host: str,
        milvus_port: int,
        collection_prefix: str = "testagent_",
        batch_size: int = _BATCH_SIZE,
    ) -> None:
        self._chroma_dir = chroma_dir
        self._milvus_host = milvus_host
        self._milvus_port = milvus_port
        self._collection_prefix = collection_prefix
        self._batch_size = batch_size
        self._chroma_client: Any = None
        self._milvus_store: MilvusVectorStore | None = None
        self._checkpoint_path = Path(chroma_dir) / _CHECKPOINT_FILE

    async def __aenter__(self) -> ChromaToMilvusMigrator:
        loop = asyncio.get_running_loop()
        self._chroma_client = await loop.run_in_executor(
            None,
            lambda: chromadb.PersistentClient(path=self._chroma_dir),
        )
        self._milvus_store = MilvusVectorStore(
            host=self._milvus_host,
            port=self._milvus_port,
            collection_prefix=self._collection_prefix,
        )
        logger.info(
            "ChromaToMilvusMigrator initialized: chroma_dir=%s, milvus=%s:%d",
            self._chroma_dir,
            self._milvus_host,
            self._milvus_port,
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._milvus_store is not None:
            await self._milvus_store.close()
            self._milvus_store = None
        self._chroma_client = None

    async def migrate_collection(self, collection_name: str) -> int:
        """Migrate a single Collection from ChromaDB to Milvus.

        1. Read full data from ChromaDB collection
        2. Create corresponding Collection in Milvus
        3. Batch write to Milvus (1000 per batch)
        4. Verify migrated row count matches source
        5. Return migrated count
        """
        self._assert_initialized()

        logger.info("Starting migration of collection: %s", collection_name)

        all_docs = await self._read_chroma_collection(collection_name)
        if not all_docs:
            logger.warning(
                "Collection '%s' is empty or does not exist in ChromaDB, skipping",
                collection_name,
            )
            return 0

        source_count = len(all_docs)
        logger.info(
            "Read %d documents from ChromaDB collection '%s'",
            source_count,
            collection_name,
        )

        docs_with_embeddings = [d for d in all_docs if d.get("embedding") is not None]
        skipped = source_count - len(docs_with_embeddings)
        if skipped > 0:
            logger.warning(
                "Skipped %d docs without embeddings in collection '%s'",
                skipped,
                collection_name,
            )

        if not docs_with_embeddings:
            logger.warning(
                "No docs with embeddings in collection '%s', skipping",
                collection_name,
            )
            return 0

        dimension = len(docs_with_embeddings[0]["embedding"])

        await self._ensure_milvus_collection(collection_name, dimension)

        migrated = await self._batch_upsert_to_milvus(collection_name, docs_with_embeddings)

        verified = await self._verify_row_count(collection_name, len(docs_with_embeddings))
        if not verified:
            raise RAGError(
                f"Row count mismatch after migration: expected {len(docs_with_embeddings)}",
                code="MIGRATION_COUNT_MISMATCH",
                details={"collection": collection_name, "expected": len(docs_with_embeddings)},
            )

        logger.info(
            "Successfully migrated %d documents from ChromaDB '%s' to Milvus",
            migrated,
            collection_name,
        )
        return migrated

    async def migrate_all(self) -> dict[str, int]:
        """Migrate all 6 RAG Collections from ChromaDB to Milvus.

        Collections: req_docs, api_docs, defect_history, test_reports, locator_library, failure_patterns
        Supports checkpoint-based resume: already-migrated Collections are skipped.
        """
        self._assert_initialized()

        checkpoint = self._load_checkpoint()
        results: dict[str, int] = {}

        for name in RAG_COLLECTIONS:
            if checkpoint.get(name, {}).get("completed", False):
                logger.info("Skipping already-migrated collection: %s", name)
                results[name] = checkpoint[name].get("count", 0)
                continue

            count = await self.migrate_collection(name)
            results[name] = count
            checkpoint[name] = {
                "completed": True,
                "count": count,
                "migrated_at": datetime.now(UTC).isoformat(),
            }
            self._save_checkpoint(checkpoint)
            logger.info("Checkpoint saved for collection: %s", name)

        logger.info("All collections migrated: %s", results)
        return results

    async def verify_migration(self, collection_name: str) -> bool:
        """Sample verification: randomly pick 10 docs, compare ChromaDB and Milvus data."""
        self._assert_initialized()

        logger.info("Starting sample verification for collection: %s", collection_name)

        chroma_docs = await self._read_chroma_collection(collection_name)
        if not chroma_docs:
            logger.warning(
                "Collection '%s' is empty in ChromaDB, nothing to verify",
                collection_name,
            )
            return True

        full_name = f"{self._collection_prefix}{collection_name}"
        assert self._milvus_store is not None
        if full_name not in self._milvus_store._collections:
            logger.error("Collection '%s' not found in Milvus cache", full_name)
            return False

        docs_with_embeddings = [d for d in chroma_docs if d.get("embedding") is not None]
        if not docs_with_embeddings:
            logger.warning(
                "No docs with embeddings in ChromaDB collection '%s'",
                collection_name,
            )
            return True

        sample_size = min(_VERIFY_SAMPLE_SIZE, len(docs_with_embeddings))
        sampled = random.sample(docs_with_embeddings, sample_size)

        client = self._milvus_store._client
        loop = asyncio.get_running_loop()

        for doc in sampled:
            doc_id = doc["id"]

            def _query_by_id(did: str = doc_id) -> list[Any]:
                result: list[Any] = client.query(
                    collection_name=full_name,
                    filter=f'id == "{did}"',
                    output_fields=["document"],
                )
                return result

            try:
                results = await loop.run_in_executor(None, _query_by_id)
            except Exception as exc:
                logger.error(
                    "Query failed for doc '%s' in Milvus: %s",
                    doc_id,
                    exc,
                )
                return False

            if not results:
                logger.error(
                    "Verification failed: doc '%s' not found in Milvus collection '%s'",
                    doc_id,
                    collection_name,
                )
                return False

            milvus_document = results[0].get("document", "")
            chroma_document = doc.get("document", "")
            if milvus_document != chroma_document:
                logger.error(
                    "Verification failed: document content mismatch for doc '%s'",
                    doc_id,
                )
                return False

        logger.info(
            "Sample verification passed for collection '%s' (%d/%d docs checked)",
            collection_name,
            sample_size,
            len(docs_with_embeddings),
        )
        return True

    def reset_checkpoint(self) -> None:
        """Reset migration checkpoint, allowing re-migration."""
        if self._checkpoint_path.exists():
            self._checkpoint_path.unlink()
            logger.info("Migration checkpoint reset")

    def _assert_initialized(self) -> None:
        if self._chroma_client is None or self._milvus_store is None:
            raise RAGError(
                "Migrator not initialized; use async context manager",
                code="MIGRATION_NOT_INITIALIZED",
            )

    async def _read_chroma_collection(self, collection_name: str) -> list[dict[str, Any]]:
        """Read all documents from a ChromaDB collection in batches."""
        assert self._chroma_client is not None
        loop = asyncio.get_running_loop()

        def _get_collection() -> Any:
            try:
                return self._chroma_client.get_collection(name=collection_name)
            except Exception:
                return None

        collection = await loop.run_in_executor(None, _get_collection)
        if collection is None:
            logger.warning("Collection '%s' not found in ChromaDB", collection_name)
            return []

        def _count() -> int:
            return int(collection.count())

        total = await loop.run_in_executor(None, _count)
        if total == 0:
            return []

        all_docs: list[dict[str, Any]] = []
        offset = 0

        while offset < total:
            current_offset = offset
            current_limit = self._batch_size

            def _read_batch(
                off: int = current_offset,
                lim: int = current_limit,
            ) -> dict[str, Any]:
                result: dict[str, Any] = collection.get(
                    include=["embeddings", "metadatas", "documents"],
                    limit=lim,
                    offset=off,
                )
                return result

            batch = await loop.run_in_executor(None, _read_batch)

            ids = batch.get("ids", [])
            embeddings = batch.get("embeddings", [])
            metadatas = batch.get("metadatas", [])
            documents = batch.get("documents", [])

            if not ids:
                break

            for i in range(len(ids)):
                doc: dict[str, Any] = {
                    "id": ids[i],
                    "embedding": embeddings[i] if i < len(embeddings) and embeddings[i] is not None else [],
                    "metadata": metadatas[i] if i < len(metadatas) and metadatas[i] is not None else {},
                    "document": documents[i] if i < len(documents) and documents[i] is not None else "",
                }
                all_docs.append(doc)

            offset += len(ids)
            logger.debug(
                "Read %d/%d docs from ChromaDB collection '%s'",
                offset,
                total,
                collection_name,
            )

        return all_docs

    async def _ensure_milvus_collection(self, name: str, dimension: int) -> None:
        """Create collection in Milvus following MilvusVectorStore schema logic."""
        assert self._milvus_store is not None
        await self._milvus_store.create_collection(name, dimension)

    async def _batch_upsert_to_milvus(
        self,
        collection_name: str,
        docs: list[dict[str, Any]],
    ) -> int:
        """Batch upsert documents to a Milvus collection."""
        assert self._milvus_store is not None
        loop = asyncio.get_running_loop()

        dimension = len(docs[0].get("embedding", [])) if docs else 1024
        await self._milvus_store.create_collection(collection_name, dimension)
        full_name = self._milvus_store._collection_name(collection_name)
        client = self._milvus_store._client

        migrated = 0

        for batch_start in range(0, len(docs), self._batch_size):
            batch = docs[batch_start : batch_start + self._batch_size]

            data: list[dict[str, Any]] = []
            for doc in batch:
                row: dict[str, Any] = {
                    "id": str(doc["id"]),
                    "embedding": doc.get("embedding", []),
                    "document": doc.get("document", ""),
                }
                metadata = doc.get("metadata", {})
                if isinstance(metadata, dict):
                    row.update(metadata)
                data.append(row)

            captured_data = list(data)

            def _upsert_batch(rows: list[dict[str, Any]] = captured_data) -> None:
                client.upsert(collection_name=full_name, data=rows)

            try:
                await loop.run_in_executor(None, _upsert_batch)
                migrated += len(batch)
                logger.debug(
                    "Upserted %d/%d docs to Milvus collection '%s'",
                    migrated,
                    len(docs),
                    collection_name,
                )
            except Exception as exc:
                raise RAGError(
                    f"Milvus batch upsert failed for collection '{collection_name}': {exc}",
                    code="MIGRATION_UPSERT_ERROR",
                    details={
                        "collection": collection_name,
                        "batch_start": batch_start,
                        "batch_size": len(batch),
                        "error": str(exc),
                    },
                ) from exc

        def _flush() -> None:
            client.flush(collection_name=full_name)

        await loop.run_in_executor(None, _flush)
        return migrated

    async def _verify_row_count(self, collection_name: str, expected: int) -> bool:
        """Verify Milvus collection row count matches source."""
        assert self._milvus_store is not None
        loop = asyncio.get_running_loop()

        full_name = self._milvus_store._collection_name(collection_name)
        client = self._milvus_store._client

        def _count() -> int:
            client.flush(collection_name=full_name)
            result = client.query(
                collection_name=full_name,
                output_fields=["count(*)"],
            )
            return result[0]["count(*)"] if result else 0

        try:
            actual = await loop.run_in_executor(None, _count)
            if actual != expected:
                logger.error(
                    "Row count mismatch for '%s': expected=%d, actual=%d",
                    collection_name,
                    expected,
                    actual,
                )
                return False
            logger.info("Row count verified for '%s': %d rows", collection_name, actual)
            return True
        except Exception as exc:
            raise RAGError(
                f"Failed to verify row count for Milvus collection '{collection_name}': {exc}",
                code="MIGRATION_VERIFY_ERROR",
                details={"collection": collection_name, "error": str(exc)},
            ) from exc

    def _load_checkpoint(self) -> dict[str, Any]:
        """Load migration checkpoint from JSON file."""
        if not self._checkpoint_path.exists():
            return {}
        try:
            data = self._checkpoint_path.read_text(encoding="utf-8")
            result: dict[str, Any] = json.loads(data)
            return result
        except Exception as exc:
            logger.warning("Failed to load checkpoint: %s, starting fresh", exc)
            return {}

    def _save_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        """Persist checkpoint state to JSON file."""
        try:
            self._checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            self._checkpoint_path.write_text(
                json.dumps(checkpoint, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.error("Failed to save checkpoint: %s", exc)


def main() -> None:
    """CLI entry point for ChromaDB to Milvus migration."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Migrate data from ChromaDB to Milvus",
    )
    parser.add_argument(
        "--chroma-dir",
        default="./.chroma_data",
        help="ChromaDB persistent directory (default: ./.chroma_data)",
    )
    parser.add_argument(
        "--milvus-host",
        default="localhost",
        help="Milvus host (default: localhost)",
    )
    parser.add_argument(
        "--milvus-port",
        type=int,
        default=19530,
        help="Milvus port (default: 19530)",
    )
    parser.add_argument(
        "--prefix",
        default="testagent_",
        help="Collection name prefix (default: testagent_)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Batch write size (default: 1000)",
    )

    args = parser.parse_args()

    async def _run() -> None:
        async with ChromaToMilvusMigrator(
            chroma_dir=args.chroma_dir,
            milvus_host=args.milvus_host,
            milvus_port=args.milvus_port,
            collection_prefix=args.prefix,
            batch_size=args.batch_size,
        ) as migrator:
            results = await migrator.migrate_all()
            all_success = True
            for collection_name, count in results.items():
                verified = await migrator.verify_migration(collection_name)
                if not verified:
                    logger.error("Verification failed for '%s'", collection_name)
                    all_success = False
                else:
                    logger.info("Verified '%s': %d rows migrated", collection_name, count)
            if all_success:
                logger.info("Migration completed successfully")
            else:
                logger.error("Migration completed with verification failures")

    asyncio.run(_run())


if __name__ == "__main__":
    main()
