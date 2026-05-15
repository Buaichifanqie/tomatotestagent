from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from testagent.rag.collections import RAG_COLLECTIONS, CollectionManager
from testagent.rag.fusion import rrf_fusion
from testagent.rag.pipeline import RAGPipeline, RAGResult


def _make_vector_search_result(
    doc_ids: list[str],
    base_score: float = 0.9,
) -> list[dict[str, Any]]:
    return [
        {
            "id": doc_id,
            "score": base_score - i * 0.1,
            "metadata": {"collection": "test_coll", "source": f"src_{doc_id}"},
            "document": f"vector content for {doc_id}",
        }
        for i, doc_id in enumerate(doc_ids)
    ]


def _make_keyword_search_result(
    doc_ids: list[str],
    base_score: float = 0.85,
) -> list[dict[str, Any]]:
    return [
        {
            "id": doc_id,
            "score": base_score - i * 0.1,
            "metadata": {"collection": "test_coll", "source": f"src_{doc_id}"},
            "document": f"keyword content for {doc_id}",
        }
        for i, doc_id in enumerate(doc_ids)
    ]


# ─────────────────────────────────────────────
# fusion.py
# ─────────────────────────────────────────────


class TestRRFFusion:
    def test_basic_fusion(self) -> None:
        vector_results = _make_vector_search_result(["A", "B", "C"], base_score=0.9)
        keyword_results = _make_keyword_search_result(["B", "C", "D"], base_score=0.85)

        fused = rrf_fusion(vector_results, keyword_results, k=60)

        assert len(fused) == 4
        ids = [r["id"] for r in fused]
        assert ids == ["B", "C", "A", "D"]

    def test_fusion_score_calculation(self) -> None:
        vector_results = [{"id": "X", "score": 1.0, "metadata": {}, "document": "x"}]
        keyword_results = [{"id": "X", "score": 0.5, "metadata": {}, "document": "x"}]

        fused = rrf_fusion(vector_results, keyword_results, k=60)

        assert len(fused) == 1
        expected = 1.0 / (60 + 1) + 1.0 / (60 + 1)
        assert fused[0]["score"] == pytest.approx(expected)

    def test_empty_vector_results(self) -> None:
        keyword_results = _make_keyword_search_result(["A", "B"])

        fused = rrf_fusion([], keyword_results, k=60)

        assert len(fused) == 2
        assert fused[0]["id"] == "A"

    def test_empty_keyword_results(self) -> None:
        vector_results = _make_vector_search_result(["A", "B"])

        fused = rrf_fusion(vector_results, [], k=60)

        assert len(fused) == 2
        assert fused[0]["id"] == "A"

    def test_both_empty(self) -> None:
        assert rrf_fusion([], [], k=60) == []

    def test_custom_k_value(self) -> None:
        vector_results = [{"id": "A", "score": 1.0, "metadata": {}, "document": "a"}]
        keyword_results = [{"id": "B", "score": 1.0, "metadata": {}, "document": "b"}]

        fused_small_k = rrf_fusion(vector_results, keyword_results, k=1)
        fused_large_k = rrf_fusion(vector_results, keyword_results, k=100)

        score_small = fused_small_k[0]["score"]
        score_large = fused_large_k[0]["score"]
        assert score_small > score_large

    def test_duplicate_id_same_rank(self) -> None:
        vector_results = [
            {"id": "A", "score": 1.0, "metadata": {}, "document": "a"},
            {"id": "A", "score": 0.9, "metadata": {}, "document": "a"},
        ]
        keyword_results: list[dict[str, Any]] = []

        fused = rrf_fusion(vector_results, keyword_results, k=60)

        assert len(fused) == 1
        expected = 1.0 / (60 + 1) + 1.0 / (60 + 2)
        assert fused[0]["score"] == pytest.approx(expected)

    def test_sort_order_descending(self) -> None:
        vector_results = _make_vector_search_result(["A", "B", "C"])
        keyword_results = _make_keyword_search_result(["D", "E"])

        fused = rrf_fusion(vector_results, keyword_results, k=60)

        scores = [r["score"] for r in fused]
        assert scores == sorted(scores, reverse=True)

    def test_result_contains_original_fields(self) -> None:
        vector_results = [
            {
                "id": "doc1",
                "score": 0.9,
                "metadata": {"type": "test"},
                "document": "hello world",
            }
        ]
        keyword_results: list[dict[str, Any]] = []

        fused = rrf_fusion(vector_results, keyword_results, k=60)

        assert len(fused) == 1
        r = fused[0]
        assert r["id"] == "doc1"
        assert r["document"] == "hello world"
        assert r["metadata"]["type"] == "test"
        assert "score" in r


# ─────────────────────────────────────────────
# collections.py
# ─────────────────────────────────────────────


class TestCollectionManager:
    def test_planner_accessible(self) -> None:
        mgr = CollectionManager()
        accessible = mgr.get_accessible_collections("planner")
        assert "req_docs" in accessible
        assert "api_docs" in accessible
        assert "defect_history" in accessible
        assert "test_reports" not in accessible
        assert "locator_library" not in accessible
        assert "failure_patterns" not in accessible

    def test_executor_accessible(self) -> None:
        mgr = CollectionManager()
        accessible = mgr.get_accessible_collections("executor")
        assert "api_docs" in accessible
        assert "locator_library" in accessible
        assert "req_docs" not in accessible
        assert "defect_history" not in accessible

    def test_analyzer_accessible(self) -> None:
        mgr = CollectionManager()
        accessible = mgr.get_accessible_collections("analyzer")
        assert "defect_history" in accessible
        assert "test_reports" in accessible
        assert "failure_patterns" in accessible
        assert "req_docs" not in accessible
        assert "api_docs" not in accessible
        assert "locator_library" not in accessible

    def test_unknown_agent_type(self) -> None:
        mgr = CollectionManager()
        accessible = mgr.get_accessible_collections("unknown")
        assert accessible == []

    def test_all_collections_covered(self) -> None:
        mgr = CollectionManager()
        all_accessible: set[str] = set()
        for agent in ("planner", "executor", "analyzer"):
            all_accessible.update(mgr.get_accessible_collections(agent))
        assert all_accessible == set(RAG_COLLECTIONS.keys())

    def test_collection_definitions_are_complete(self) -> None:
        expected_keys = {
            "req_docs",
            "api_docs",
            "defect_history",
            "test_reports",
            "locator_library",
            "failure_patterns",
        }
        assert set(RAG_COLLECTIONS.keys()) == expected_keys
        for _name, info in RAG_COLLECTIONS.items():
            assert "description" in info
            assert "access" in info
            assert isinstance(info["access"], list)


# ─────────────────────────────────────────────
# pipeline.py
# ─────────────────────────────────────────────


class TestRAGPipeline:
    @pytest.fixture
    def mock_embedding(self) -> MagicMock:
        svc = MagicMock()

        async def embed_side(text: str) -> list[float]:
            return [0.1, 0.2, 0.3]

        async def embed_batch_side(texts: list[str]) -> list[list[float]]:
            return [[0.1, 0.2, 0.3]] * len(texts)

        svc.embed = AsyncMock(side_effect=embed_side)
        svc.embed_batch = AsyncMock(side_effect=embed_batch_side)
        return svc

    @pytest.fixture
    def mock_vector_store(self) -> MagicMock:
        store = MagicMock()
        store.upsert = AsyncMock()
        store.delete = AsyncMock()

        async def search_side(
            query_vector: list[float],
            top_k: int = 10,
            filters: dict[str, Any] | None = None,
        ) -> list[dict[str, Any]]:
            coll = (filters or {}).get("collection", "")
            if coll == "empty_coll":
                return []
            if top_k > 2:
                top_k = 2
            return [
                {
                    "id": f"vec_doc{i}",
                    "score": 0.9 - (i - 1) * 0.1,
                    "metadata": {"collection": coll},
                    "document": f"vector result {i}",
                }
                for i in range(1, top_k + 1)
            ]

        store.search = AsyncMock(side_effect=search_side)
        return store

    @pytest.fixture
    def mock_fulltext(self) -> MagicMock:
        ft = MagicMock()
        ft.index = AsyncMock()
        ft.delete = AsyncMock()

        async def search_side(
            query: str,
            top_k: int = 10,
            filters: dict[str, Any] | None = None,
        ) -> list[dict[str, Any]]:
            coll = (filters or {}).get("collection", "")
            if coll == "empty_coll":
                return []
            if top_k > 2:
                top_k = 2
            return [
                {
                    "id": f"kw_doc{i}",
                    "score": 0.85 - (i - 1) * 0.1,
                    "metadata": {"collection": coll},
                    "document": f"keyword result {i}",
                }
                for i in range(1, top_k + 1)
            ]

        ft.search = AsyncMock(side_effect=search_side)
        return ft

    @pytest.fixture
    def pipeline(
        self,
        mock_embedding: MagicMock,
        mock_vector_store: MagicMock,
        mock_fulltext: MagicMock,
    ) -> RAGPipeline:
        return RAGPipeline(
            embedding_service=mock_embedding,
            vector_store=mock_vector_store,
            fulltext=mock_fulltext,
        )

    async def test_ingest_success(
        self,
        pipeline: RAGPipeline,
        mock_embedding: MagicMock,
        mock_vector_store: MagicMock,
        mock_fulltext: MagicMock,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            source_file = Path(tmpdir) / "test.md"
            source_file.write_text(
                "# Title\n\n## Section 1\n\nContent 1.\n\n## Section 2\n\nContent 2.",
                encoding="utf-8",
            )

            chunk_count = await pipeline.ingest(
                source=str(source_file),
                collection="test_coll",
                metadata={"source_type": "test"},
            )

            assert chunk_count > 0
            mock_embedding.embed_batch.assert_awaited_once()
            mock_vector_store.upsert.assert_awaited_once()
            mock_fulltext.index.assert_awaited_once()

            upsert_called: list[dict[str, Any]] = mock_vector_store.upsert.call_args[0][0]
            index_called: list[dict[str, Any]] = mock_fulltext.index.call_args[0][0]
            assert len(upsert_called) == chunk_count
            assert len(index_called) == chunk_count
            for doc in upsert_called:
                assert "id" in doc
                assert "embedding" in doc
                assert "metadata" in doc
                assert "document" in doc

    async def test_ingest_empty_file(
        self,
        pipeline: RAGPipeline,
        mock_embedding: MagicMock,
        mock_vector_store: MagicMock,
        mock_fulltext: MagicMock,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            source_file = Path(tmpdir) / "empty.md"
            source_file.write_text("   \n\n  ", encoding="utf-8")

            chunk_count = await pipeline.ingest(
                source=str(source_file),
                collection="test_coll",
            )

            assert chunk_count == 0
            mock_embedding.embed_batch.assert_not_called()
            mock_vector_store.upsert.assert_not_called()
            mock_fulltext.index.assert_not_called()

    async def test_query_hybrid_search(
        self,
        pipeline: RAGPipeline,
        mock_embedding: MagicMock,
        mock_vector_store: MagicMock,
        mock_fulltext: MagicMock,
    ) -> None:
        results = await pipeline.query(
            query_text="test query",
            collection="test_coll",
            top_k=3,
        )

        assert len(results) <= 3
        mock_embedding.embed.assert_awaited_once_with("test query")
        mock_vector_store.search.assert_awaited_once()
        mock_fulltext.search.assert_awaited_once()

        vec_call_kwargs = mock_vector_store.search.call_args.kwargs
        assert vec_call_kwargs["top_k"] == 6
        assert "collection" in vec_call_kwargs["filters"]

        kw_call_kwargs = mock_fulltext.search.call_args.kwargs
        assert kw_call_kwargs["top_k"] == 6
        assert "collection" in kw_call_kwargs["filters"]

    async def test_query_results_are_ragresult_objects(
        self,
        pipeline: RAGPipeline,
    ) -> None:
        results = await pipeline.query(
            query_text="find me",
            collection="test_coll",
            top_k=5,
        )

        for r in results:
            assert isinstance(r, RAGResult)
            assert isinstance(r.doc_id, str)
            assert isinstance(r.content, str)
            assert isinstance(r.score, float)
            assert isinstance(r.metadata, dict)

    async def test_query_with_filters(
        self,
        pipeline: RAGPipeline,
        mock_vector_store: MagicMock,
        mock_fulltext: MagicMock,
    ) -> None:
        await pipeline.query(
            query_text="filtered search",
            collection="test_coll",
            top_k=5,
            filters={"severity": "critical"},
        )

        vec_filters = mock_vector_store.search.call_args.kwargs["filters"]
        assert vec_filters["collection"] == "test_coll"
        assert vec_filters["severity"] == "critical"

        kw_filters = mock_fulltext.search.call_args.kwargs["filters"]
        assert kw_filters["collection"] == "test_coll"
        assert kw_filters["severity"] == "critical"

    async def test_query_zero_results(
        self,
        pipeline: RAGPipeline,
    ) -> None:
        results = await pipeline.query(
            query_text="nonexistent",
            collection="empty_coll",
            top_k=5,
        )

        assert results == []

    async def test_write_back(
        self,
        pipeline: RAGPipeline,
        mock_embedding: MagicMock,
        mock_vector_store: MagicMock,
        mock_fulltext: MagicMock,
    ) -> None:
        await pipeline.write_back(
            content="This is analysis result content for write-back.",
            collection="defect_history",
            metadata={"severity": "critical", "source_agent": "analyzer"},
        )

        mock_embedding.embed_batch.assert_awaited_once()
        mock_vector_store.upsert.assert_awaited_once()
        mock_fulltext.index.assert_awaited_once()

        upsert_called: list[dict[str, Any]] = mock_vector_store.upsert.call_args[0][0]
        assert len(upsert_called) > 0
        for doc in upsert_called:
            assert "id" in doc
            assert "embedding" in doc
            assert "metadata" in doc
            assert "document" in doc
            assert doc["metadata"]["collection"] == "defect_history"
            assert doc["metadata"]["source"] == "write_back"
            assert doc["metadata"]["severity"] == "critical"
            assert doc["metadata"]["source_agent"] == "analyzer"
            assert doc["id"].endswith("_wb_0000")

    async def test_write_back_small_content(
        self,
        pipeline: RAGPipeline,
        mock_embedding: MagicMock,
        mock_vector_store: MagicMock,
        mock_fulltext: MagicMock,
    ) -> None:
        await pipeline.write_back(
            content="Short content.",
            collection="test_reports",
            metadata={"source": "test"},
        )

        upsert_called: list[dict[str, Any]] = mock_vector_store.upsert.call_args[0][0]
        assert len(upsert_called) == 1
        assert "Short content." in upsert_called[0]["document"]

    async def test_ingest_metadata_propagation(
        self,
        pipeline: RAGPipeline,
        mock_embedding: MagicMock,
        mock_vector_store: MagicMock,
    ) -> None:
        with TemporaryDirectory() as tmpdir:
            source_file = Path(tmpdir) / "doc.md"
            source_file.write_text("## Header\n\nContent.", encoding="utf-8")

            await pipeline.ingest(
                source=str(source_file),
                collection="req_docs",
                metadata={"version": "1.0", "author": "tester"},
            )

            upsert_called: list[dict[str, Any]] = mock_vector_store.upsert.call_args[0][0]
            for doc in upsert_called:
                meta = doc["metadata"]
                assert meta["collection"] == "req_docs"
                assert meta["version"] == "1.0"
                assert meta["author"] == "tester"
                assert "source" in meta
                assert "file_type" in meta
                assert "chunk_type" in meta

    async def test_query_top_k_enforcement(
        self,
        pipeline: RAGPipeline,
        mock_embedding: MagicMock,
    ) -> None:
        results = await pipeline.query(
            query_text="test",
            collection="test_coll",
            top_k=1,
        )

        assert len(results) <= 1
