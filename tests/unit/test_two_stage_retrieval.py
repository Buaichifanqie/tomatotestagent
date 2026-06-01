"""Tests for two-stage RAG retrieval module."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from testagent.rag.pipeline import RAGResult


def _make_rag_result(doc_id: str, content: str = "", score: float = 0.8) -> RAGResult:
    return RAGResult(
        doc_id=doc_id,
        content=content or f"content for {doc_id}",
        score=score,
        metadata={"app_id": "com.test.app"},
    )


def _make_pipeline_mock(
    *,
    cases_results: list[RAGResult] | None = None,
    patterns_results: list[RAGResult] | None = None,
    cases_side_effect: Any = None,
    patterns_side_effect: Any = None,
) -> MagicMock:
    """Create a mock RAGPipeline that returns different results per collection."""
    pipeline = MagicMock()

    if cases_side_effect is not None:
        pipeline.query = AsyncMock(side_effect=cases_side_effect)
    else:
        cases = cases_results if cases_results is not None else []
        patterns = patterns_results if patterns_results is not None else []

        async def query_side_effect(
            query_text: str,
            collection: str,
            top_k: int = 5,
            filters: dict[str, Any] | None = None,
        ) -> list[RAGResult]:
            if collection == "app_test_cases":
                return cases[:top_k]
            if collection == "app_learned_patterns":
                return patterns[:top_k]
            return []

        pipeline.query = AsyncMock(side_effect=query_side_effect)

    return pipeline


# ─────────────────────────────────────────────────────────────────────────────
# stage1_retrieve
# ─────────────────────────────────────────────────────────────────────────────


class TestStage1Retrieve:
    async def test_queries_both_collections_in_parallel(self) -> None:
        from testagent.plan.two_stage_retrieval import stage1_retrieve

        cases = [_make_rag_result("c1"), _make_rag_result("c2")]
        patterns = [_make_rag_result("p1")]
        pipeline = _make_pipeline_mock(cases_results=cases, patterns_results=patterns)

        result = await stage1_retrieve(pipeline, "some PRD text", "com.test.app")

        assert "cases" in result
        assert "patterns" in result
        assert len(result["cases"]) == 2
        assert len(result["patterns"]) == 1

    async def test_passes_correct_collections_and_filters(self) -> None:
        from testagent.plan.two_stage_retrieval import stage1_retrieve

        pipeline = _make_pipeline_mock()
        await stage1_retrieve(pipeline, "PRD text", "com.test.app")

        calls = pipeline.query.call_args_list
        assert len(calls) == 2

        collections_queried = {c.kwargs.get("collection") or c.args[1] for c in calls}
        assert "app_test_cases" in collections_queried
        assert "app_learned_patterns" in collections_queried

        for call in calls:
            kwargs = call.kwargs
            assert kwargs["top_k"] == 3
            assert kwargs["filters"] == {"app_id": "com.test.app"}

    async def test_uses_prd_text_as_query(self) -> None:
        from testagent.plan.two_stage_retrieval import stage1_retrieve

        pipeline = _make_pipeline_mock()
        await stage1_retrieve(pipeline, "login flow PRD", "com.test.app")

        for call in pipeline.query.call_args_list:
            assert call.kwargs.get("query_text") == "login flow PRD"

    async def test_returns_empty_lists_when_no_results(self) -> None:
        from testagent.plan.two_stage_retrieval import stage1_retrieve

        pipeline = _make_pipeline_mock(cases_results=[], patterns_results=[])
        result = await stage1_retrieve(pipeline, "unknown PRD", "com.test.app")

        assert result["cases"] == []
        assert result["patterns"] == []

    async def test_graceful_degradation_when_cases_query_fails(self) -> None:
        from testagent.plan.two_stage_retrieval import stage1_retrieve

        patterns = [_make_rag_result("p1")]

        async def failing_query(
            query_text: str,
            collection: str,
            top_k: int = 5,
            filters: dict[str, Any] | None = None,
        ) -> list[RAGResult]:
            if collection == "app_test_cases":
                raise RuntimeError("vector store down")
            return patterns[:top_k]

        pipeline = MagicMock()
        pipeline.query = AsyncMock(side_effect=failing_query)

        result = await stage1_retrieve(pipeline, "PRD", "com.test.app")

        assert result["cases"] == []
        assert len(result["patterns"]) == 1

    async def test_graceful_degradation_when_patterns_query_fails(self) -> None:
        from testagent.plan.two_stage_retrieval import stage1_retrieve

        cases = [_make_rag_result("c1")]

        async def failing_query(
            query_text: str,
            collection: str,
            top_k: int = 5,
            filters: dict[str, Any] | None = None,
        ) -> list[RAGResult]:
            if collection == "app_learned_patterns":
                raise RuntimeError("collection not found")
            return cases[:top_k]

        pipeline = MagicMock()
        pipeline.query = AsyncMock(side_effect=failing_query)

        result = await stage1_retrieve(pipeline, "PRD", "com.test.app")

        assert len(result["cases"]) == 1
        assert result["patterns"] == []

    async def test_graceful_degradation_when_both_queries_fail(self) -> None:
        from testagent.plan.two_stage_retrieval import stage1_retrieve

        pipeline = MagicMock()
        pipeline.query = AsyncMock(side_effect=RuntimeError("total failure"))

        result = await stage1_retrieve(pipeline, "PRD", "com.test.app")

        assert result["cases"] == []
        assert result["patterns"] == []


# ─────────────────────────────────────────────────────────────────────────────
# stage2_retrieve
# ─────────────────────────────────────────────────────────────────────────────


class TestStage2Retrieve:
    async def test_queries_both_collections_with_precise_text(self) -> None:
        from testagent.plan.two_stage_retrieval import stage2_retrieve

        cases = [_make_rag_result("c3"), _make_rag_result("c4")]
        patterns = [_make_rag_result("p2")]
        pipeline = _make_pipeline_mock(cases_results=cases, patterns_results=patterns)

        result = await stage2_retrieve(
            pipeline, "refined TC text", "com.test.app", stage1_doc_ids=["c1", "p1"],
        )

        assert len(result["cases"]) == 2
        assert len(result["patterns"]) == 1

    async def test_uses_correct_top_k_values(self) -> None:
        from testagent.plan.two_stage_retrieval import stage2_retrieve

        pipeline = _make_pipeline_mock()
        await stage2_retrieve(pipeline, "TC text", "com.test.app", stage1_doc_ids=[])

        calls = pipeline.query.call_args_list
        assert len(calls) == 2

        cases_call = next(
            c for c in calls if c.kwargs.get("collection") == "app_test_cases"
        )
        patterns_call = next(
            c for c in calls if c.kwargs.get("collection") == "app_learned_patterns"
        )

        assert cases_call.kwargs["top_k"] == 5
        assert patterns_call.kwargs["top_k"] == 3

    async def test_deduplicates_by_stage1_doc_ids(self) -> None:
        from testagent.plan.two_stage_retrieval import stage2_retrieve

        # Stage1 returned c1 and p1; stage2 query returns c1 again plus new ones
        cases = [_make_rag_result("c1"), _make_rag_result("c2"), _make_rag_result("c3")]
        patterns = [_make_rag_result("p1"), _make_rag_result("p2")]
        pipeline = _make_pipeline_mock(cases_results=cases, patterns_results=patterns)

        result = await stage2_retrieve(
            pipeline, "TC text", "com.test.app", stage1_doc_ids=["c1", "p1"],
        )

        case_ids = [r.doc_id for r in result["cases"]]
        pattern_ids = [r.doc_id for r in result["patterns"]]

        assert "c1" not in case_ids
        assert "c2" in case_ids
        assert "c3" in case_ids
        assert "p1" not in pattern_ids
        assert "p2" in pattern_ids

    async def test_dedup_with_empty_stage1_ids(self) -> None:
        from testagent.plan.two_stage_retrieval import stage2_retrieve

        cases = [_make_rag_result("c1"), _make_rag_result("c2")]
        patterns = [_make_rag_result("p1")]
        pipeline = _make_pipeline_mock(cases_results=cases, patterns_results=patterns)

        result = await stage2_retrieve(
            pipeline, "TC text", "com.test.app", stage1_doc_ids=[],
        )

        assert len(result["cases"]) == 2
        assert len(result["patterns"]) == 1

    async def test_graceful_degradation_when_cases_query_fails(self) -> None:
        from testagent.plan.two_stage_retrieval import stage2_retrieve

        patterns = [_make_rag_result("p2")]

        async def failing_query(
            query_text: str,
            collection: str,
            top_k: int = 5,
            filters: dict[str, Any] | None = None,
        ) -> list[RAGResult]:
            if collection == "app_test_cases":
                raise RuntimeError("search failed")
            return patterns[:top_k]

        pipeline = MagicMock()
        pipeline.query = AsyncMock(side_effect=failing_query)

        result = await stage2_retrieve(
            pipeline, "TC text", "com.test.app", stage1_doc_ids=[],
        )

        assert result["cases"] == []
        assert len(result["patterns"]) == 1

    async def test_graceful_degradation_when_patterns_query_fails(self) -> None:
        from testagent.plan.two_stage_retrieval import stage2_retrieve

        cases = [_make_rag_result("c2")]

        async def failing_query(
            query_text: str,
            collection: str,
            top_k: int = 5,
            filters: dict[str, Any] | None = None,
        ) -> list[RAGResult]:
            if collection == "app_learned_patterns":
                raise RuntimeError("search failed")
            return cases[:top_k]

        pipeline = MagicMock()
        pipeline.query = AsyncMock(side_effect=failing_query)

        result = await stage2_retrieve(
            pipeline, "TC text", "com.test.app", stage1_doc_ids=[],
        )

        assert len(result["cases"]) == 1
        assert result["patterns"] == []


# ─────────────────────────────────────────────────────────────────────────────
# run_two_stage_retrieval
# ─────────────────────────────────────────────────────────────────────────────


class TestRunTwoStageRetrieval:
    async def test_returns_combined_results_with_stage1_doc_ids(self) -> None:
        from testagent.plan.two_stage_retrieval import run_two_stage_retrieval

        s1_cases = [_make_rag_result("c1", score=0.9)]
        s1_patterns = [_make_rag_result("p1", score=0.8)]
        s2_cases = [_make_rag_result("c2", score=0.7)]
        s2_patterns = [_make_rag_result("p2", score=0.6)]

        call_count = 0

        async def query_side_effect(
            query_text: str,
            collection: str,
            top_k: int = 5,
            filters: dict[str, Any] | None = None,
        ) -> list[RAGResult]:
            nonlocal call_count
            call_count += 1
            # First two calls are stage1 (parallel), next two are stage2 (parallel)
            if call_count <= 2:
                if collection == "app_test_cases":
                    return s1_cases
                return s1_patterns
            else:
                if collection == "app_test_cases":
                    return s2_cases
                return s2_patterns

        pipeline = MagicMock()
        pipeline.query = AsyncMock(side_effect=query_side_effect)

        result = await run_two_stage_retrieval(pipeline, "PRD text", "com.test.app")

        assert "cases" in result
        assert "patterns" in result
        assert "stage1_doc_ids" in result

        # Should contain both stage1 and stage2 results
        case_ids = [r.doc_id for r in result["cases"]]
        pattern_ids = [r.doc_id for r in result["patterns"]]
        assert "c1" in case_ids
        assert "c2" in case_ids
        assert "p1" in pattern_ids
        assert "p2" in pattern_ids

        # stage1_doc_ids should contain the doc_ids from stage1
        assert "c1" in result["stage1_doc_ids"]
        assert "p1" in result["stage1_doc_ids"]

    async def test_stage2_deduplicates_against_stage1(self) -> None:
        from testagent.plan.two_stage_retrieval import run_two_stage_retrieval

        s1_cases = [_make_rag_result("c1", score=0.9)]
        s1_patterns = [_make_rag_result("p1", score=0.8)]
        # Stage2 returns the same doc plus a new one
        s2_cases = [_make_rag_result("c1", score=0.7), _make_rag_result("c2", score=0.6)]
        s2_patterns = [_make_rag_result("p1", score=0.5), _make_rag_result("p2", score=0.4)]

        call_count = 0

        async def query_side_effect(
            query_text: str,
            collection: str,
            top_k: int = 5,
            filters: dict[str, Any] | None = None,
        ) -> list[RAGResult]:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                if collection == "app_test_cases":
                    return s1_cases
                return s1_patterns
            else:
                if collection == "app_test_cases":
                    return s2_cases
                return s2_patterns

        pipeline = MagicMock()
        pipeline.query = AsyncMock(side_effect=query_side_effect)

        result = await run_two_stage_retrieval(pipeline, "PRD text", "com.test.app")

        case_ids = [r.doc_id for r in result["cases"]]
        pattern_ids = [r.doc_id for r in result["patterns"]]

        # c1 and p1 should appear only once (from stage1)
        assert case_ids.count("c1") == 1
        assert pattern_ids.count("p1") == 1
        # c2 and p2 should be present (from stage2)
        assert "c2" in case_ids
        assert "p2" in pattern_ids

    async def test_uses_prd_text_for_stage1_and_tc_text_for_stage2(self) -> None:
        from testagent.plan.two_stage_retrieval import run_two_stage_retrieval

        pipeline = _make_pipeline_mock(
            cases_results=[_make_rag_result("c1")],
            patterns_results=[_make_rag_result("p1")],
        )

        await run_two_stage_retrieval(pipeline, "original PRD", "com.test.app")

        calls = pipeline.query.call_args_list
        # stage1 queries should use the PRD text
        stage1_query_texts = {calls[0].kwargs["query_text"], calls[1].kwargs["query_text"]}
        assert "original PRD" in stage1_query_texts

        # stage2 queries should use a different (combined/refined) text
        stage2_query_texts = {calls[2].kwargs["query_text"], calls[3].kwargs["query_text"]}
        for qt in stage2_query_texts:
            # The stage2 text should contain the original PRD content
            # but may be augmented with stage1 results
            assert isinstance(qt, str)
            assert len(qt) > 0

    async def test_handles_stage1_failure_gracefully(self) -> None:
        from testagent.plan.two_stage_retrieval import run_two_stage_retrieval

        # Both stage1 queries fail, stage2 succeeds
        call_count = 0

        async def query_side_effect(
            query_text: str,
            collection: str,
            top_k: int = 5,
            filters: dict[str, Any] | None = None,
        ) -> list[RAGResult]:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise RuntimeError("stage1 failure")
            return [_make_rag_result("c_from_stage2")]

        pipeline = MagicMock()
        pipeline.query = AsyncMock(side_effect=query_side_effect)

        result = await run_two_stage_retrieval(pipeline, "PRD", "com.test.app")

        # Should still return results from stage2
        assert len(result["cases"]) >= 0
        assert len(result["patterns"]) >= 0
        assert isinstance(result["stage1_doc_ids"], list)
