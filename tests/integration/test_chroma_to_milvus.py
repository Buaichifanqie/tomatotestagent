from __future__ import annotations

import contextlib
import os
import shutil
import socket
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from testagent.rag.migrate_chroma_to_milvus import ChromaToMilvusMigrator
from testagent.rag.vector_store import ChromaDBVectorStore

MILVUS_HOST = os.environ.get("TESTAGENT_MILVUS_HOST", "localhost")
MILVUS_PORT = int(os.environ.get("TESTAGENT_MILVUS_PORT", "19530"))
TEST_DIMENSION = 8
TEST_COLLECTIONS = ["req_docs", "api_docs", "defect_history"]
_CHROMA_TEST_DIR = Path(__file__).resolve().parent.parent / "_test_data" / "chroma_migration"


def _milvus_is_available() -> bool:
    try:
        with socket.create_connection((MILVUS_HOST, MILVUS_PORT), timeout=2):
            return True
    except OSError:
        return False


requires_milvus = pytest.mark.skipif(
    not _milvus_is_available(),
    reason="Milvus not available; set TESTAGENT_MILVUS_HOST and ensure Milvus is running",
)


def _generate_embedding(dim: int = TEST_DIMENSION) -> list[float]:
    return [0.1 * (i + 1) for i in range(dim)]


async def _populate_chroma_collection(
    chroma_dir: str,
    collection_name: str,
    num_docs: int = 5,
) -> list[dict[str, Any]]:
    store = ChromaDBVectorStore(
        persist_dir=chroma_dir,
        collection_name=collection_name,
    )
    docs: list[dict[str, Any]] = []
    for i in range(num_docs):
        docs.append(
            {
                "id": f"{collection_name}_doc_{i:04d}",
                "embedding": _generate_embedding(),
                "metadata": {
                    "collection": collection_name,
                    "index": i,
                    "source": "test",
                },
                "document": f"Test document {i} for collection {collection_name}",
            }
        )
    await store.upsert(docs)
    return docs


@pytest_asyncio.fixture
async def chroma_with_data() -> str:
    if _CHROMA_TEST_DIR.exists():
        shutil.rmtree(_CHROMA_TEST_DIR, ignore_errors=True)
    _CHROMA_TEST_DIR.mkdir(parents=True, exist_ok=True)
    chroma_dir = str(_CHROMA_TEST_DIR)

    for name in TEST_COLLECTIONS:
        await _populate_chroma_collection(chroma_dir, name, num_docs=5)

    yield chroma_dir

    if _CHROMA_TEST_DIR.exists():
        shutil.rmtree(_CHROMA_TEST_DIR, ignore_errors=True)


@pytest_asyncio.fixture
async def empty_chroma() -> str:
    empty_dir = _CHROMA_TEST_DIR.parent / "chroma_migration_empty"
    if empty_dir.exists():
        shutil.rmtree(empty_dir, ignore_errors=True)
    empty_dir.mkdir(parents=True, exist_ok=True)
    yield str(empty_dir)
    if empty_dir.exists():
        shutil.rmtree(empty_dir, ignore_errors=True)


async def _cleanup_milvus_collections(prefix: str = "testagent_") -> None:
    try:
        from pymilvus import MilvusClient

        client = MilvusClient(uri=f"http://{MILVUS_HOST}:{MILVUS_PORT}")
        for name in ["req_docs", "api_docs", "defect_history", "test_reports", "locator_library", "failure_patterns"]:
            full_name = f"{prefix}{name}"
            with contextlib.suppress(Exception):
                client.drop_collection(full_name)
        client.close()
    except Exception:
        pass


class TestSingleCollectionMigration:
    @requires_milvus
    @pytest.mark.integration
    async def test_migrate_single_collection(self, chroma_with_data: str) -> None:
        await _cleanup_milvus_collections()

        try:
            async with ChromaToMilvusMigrator(
                chroma_dir=chroma_with_data,
                milvus_host=MILVUS_HOST,
                milvus_port=MILVUS_PORT,
            ) as migrator:
                migrator.reset_checkpoint()
                count = await migrator.migrate_collection("req_docs")

            assert count == 5
        finally:
            await _cleanup_milvus_collections()

    @requires_milvus
    @pytest.mark.integration
    async def test_migrate_empty_collection(self, empty_chroma: str) -> None:
        await _cleanup_milvus_collections()

        try:
            async with ChromaToMilvusMigrator(
                chroma_dir=empty_chroma,
                milvus_host=MILVUS_HOST,
                milvus_port=MILVUS_PORT,
            ) as migrator:
                migrator.reset_checkpoint()
                count = await migrator.migrate_collection("nonexistent_collection")

            assert count == 0
        finally:
            await _cleanup_milvus_collections()

    @requires_milvus
    @pytest.mark.integration
    async def test_migrate_collection_with_metadata(self, chroma_with_data: str) -> None:
        await _cleanup_milvus_collections()

        try:
            async with ChromaToMilvusMigrator(
                chroma_dir=chroma_with_data,
                milvus_host=MILVUS_HOST,
                milvus_port=MILVUS_PORT,
            ) as migrator:
                migrator.reset_checkpoint()
                count = await migrator.migrate_collection("api_docs")

            assert count == 5
        finally:
            await _cleanup_milvus_collections()


class TestFullMigration:
    @requires_milvus
    @pytest.mark.integration
    async def test_migrate_all_collections(self, chroma_with_data: str) -> None:
        await _cleanup_milvus_collections()

        try:
            async with ChromaToMilvusMigrator(
                chroma_dir=chroma_with_data,
                milvus_host=MILVUS_HOST,
                milvus_port=MILVUS_PORT,
            ) as migrator:
                migrator.reset_checkpoint()
                results = await migrator.migrate_all()

            for name in TEST_COLLECTIONS:
                assert name in results
                assert results[name] == 5
        finally:
            await _cleanup_milvus_collections()

    @requires_milvus
    @pytest.mark.integration
    async def test_checkpoint_resume(self, chroma_with_data: str) -> None:
        await _cleanup_milvus_collections()

        try:
            async with ChromaToMilvusMigrator(
                chroma_dir=chroma_with_data,
                milvus_host=MILVUS_HOST,
                milvus_port=MILVUS_PORT,
            ) as migrator:
                migrator.reset_checkpoint()
                first_results = await migrator.migrate_all()

            async with ChromaToMilvusMigrator(
                chroma_dir=chroma_with_data,
                milvus_host=MILVUS_HOST,
                milvus_port=MILVUS_PORT,
            ) as migrator:
                second_results = await migrator.migrate_all()

            for name in TEST_COLLECTIONS:
                assert second_results[name] == first_results[name]
        finally:
            await _cleanup_milvus_collections()

    @requires_milvus
    @pytest.mark.integration
    async def test_reset_checkpoint(self, chroma_with_data: str) -> None:
        await _cleanup_milvus_collections()

        try:
            async with ChromaToMilvusMigrator(
                chroma_dir=chroma_with_data,
                milvus_host=MILVUS_HOST,
                milvus_port=MILVUS_PORT,
            ) as migrator:
                migrator.reset_checkpoint()
                await migrator.migrate_all()

                checkpoint = migrator._load_checkpoint()
                assert "req_docs" in checkpoint
                assert checkpoint["req_docs"]["completed"] is True

                migrator.reset_checkpoint()
                checkpoint_after = migrator._load_checkpoint()
                assert checkpoint_after == {}
        finally:
            await _cleanup_milvus_collections()


class TestMigrationVerification:
    @requires_milvus
    @pytest.mark.integration
    async def test_verify_after_migration(self, chroma_with_data: str) -> None:
        await _cleanup_milvus_collections()

        try:
            async with ChromaToMilvusMigrator(
                chroma_dir=chroma_with_data,
                milvus_host=MILVUS_HOST,
                milvus_port=MILVUS_PORT,
            ) as migrator:
                migrator.reset_checkpoint()
                await migrator.migrate_collection("req_docs")
                verified = await migrator.verify_migration("req_docs")

            assert verified is True
        finally:
            await _cleanup_milvus_collections()

    @requires_milvus
    @pytest.mark.integration
    async def test_verify_all_collections_after_migrate_all(self, chroma_with_data: str) -> None:
        await _cleanup_milvus_collections()

        try:
            async with ChromaToMilvusMigrator(
                chroma_dir=chroma_with_data,
                milvus_host=MILVUS_HOST,
                milvus_port=MILVUS_PORT,
            ) as migrator:
                migrator.reset_checkpoint()
                await migrator.migrate_all()

                for name in TEST_COLLECTIONS:
                    verified = await migrator.verify_migration(name)
                    assert verified is True, f"Verification failed for collection '{name}'"
        finally:
            await _cleanup_milvus_collections()

    @requires_milvus
    @pytest.mark.integration
    async def test_verify_empty_collection(self, empty_chroma: str) -> None:
        await _cleanup_milvus_collections()

        try:
            async with ChromaToMilvusMigrator(
                chroma_dir=empty_chroma,
                milvus_host=MILVUS_HOST,
                milvus_port=MILVUS_PORT,
            ) as migrator:
                migrator.reset_checkpoint()
                verified = await migrator.verify_migration("nonexistent_collection")

            assert verified is True
        finally:
            await _cleanup_milvus_collections()


class TestMigratorLifecycle:
    async def test_migrator_not_initialized_raises(self) -> None:
        migrator = ChromaToMilvusMigrator(
            chroma_dir="/tmp/nonexistent",
            milvus_host="localhost",
            milvus_port=19530,
        )
        with pytest.raises(Exception, match="MIGRATION_NOT_INITIALIZED"):
            await migrator.migrate_collection("req_docs")

    async def test_checkpoint_persistence(self, chroma_with_data: str) -> None:
        migrator = ChromaToMilvusMigrator(
            chroma_dir=chroma_with_data,
            milvus_host=MILVUS_HOST,
            milvus_port=MILVUS_PORT,
        )

        checkpoint: dict[str, Any] = {
            "req_docs": {
                "completed": True,
                "count": 5,
                "migrated_at": "2026-01-01T00:00:00+00:00",
            }
        }
        migrator._save_checkpoint(checkpoint)

        loaded = migrator._load_checkpoint()
        assert loaded == checkpoint

        migrator.reset_checkpoint()
        assert migrator._load_checkpoint() == {}
