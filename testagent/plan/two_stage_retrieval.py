"""Two-stage RAG retrieval for App Context Memory.

Stage 1: Broad parallel retrieval of historical cases and learned patterns.
Stage 2: Precise re-query with deduplication against stage 1 results.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from testagent.common.logging import get_logger

if TYPE_CHECKING:
    from testagent.rag.pipeline import RAGPipeline, RAGResult

logger = get_logger(__name__)

FILTER_KEY = "app_id"
CASES_COLLECTION = "app_test_cases"
PATTERNS_COLLECTION = "app_learned_patterns"
DOCS_COLLECTION = "app_documentation"


async def _query_collection(
    pipeline: RAGPipeline,
    query_text: str,
    collection: str,
    top_k: int,
    app_id: str,
) -> list[RAGResult]:
    """Query a single collection with error handling.

    Returns an empty list on failure rather than propagating the exception.
    """
    try:
        return await pipeline.query(
            query_text=query_text,
            collection=collection,
            top_k=top_k,
            filters={FILTER_KEY: app_id},
        )
    except Exception as exc:
        logger.warning(
            "Retrieval from '%s' failed: %s",
            collection,
            exc,
            exc_info=exc,
            extra={"extra_data": {"collection": collection, "app_id": app_id}},
        )
        return []


def _build_stage2_query(prd_text: str, stage1_cases: list[RAGResult]) -> str:
    """Build a refined query for stage 2 by combining PRD with stage 1 case summaries."""
    if not stage1_cases:
        return prd_text

    case_titles: list[str] = []
    for r in stage1_cases:
        # Extract first line as a summary (typically contains the case title)
        first_line = r.content.split("\n", 1)[0].strip()
        if first_line:
            case_titles.append(first_line)

    if not case_titles:
        return prd_text

    summary = "; ".join(case_titles)
    return f"{prd_text}\n\n参考已有用例: {summary}"


async def stage1_retrieve(
    pipeline: RAGPipeline,
    prd_text: str,
    app_id: str,
) -> dict[str, list[RAGResult]]:
    """Stage 1: Broad retrieval of cases, patterns, and docs in parallel.

    Args:
        pipeline: The RAG pipeline instance.
        prd_text: The PRD / requirement text to query against.
        app_id: The app identifier for filtering.

    Returns:
        ``{"cases": [...], "patterns": [...], "docs": [...]}``
    """
    cases_task = _query_collection(
        pipeline, prd_text, CASES_COLLECTION, top_k=3, app_id=app_id,
    )
    patterns_task = _query_collection(
        pipeline, prd_text, PATTERNS_COLLECTION, top_k=3, app_id=app_id,
    )
    docs_task = _query_collection(
        pipeline, prd_text, DOCS_COLLECTION, top_k=2, app_id=app_id,
    )

    cases, patterns, docs = await asyncio.gather(cases_task, patterns_task, docs_task)

    logger.info(
        "Stage 1 retrieval for app '%s': %d cases, %d patterns, %d docs",
        app_id,
        len(cases),
        len(patterns),
        len(docs),
        extra={"extra_data": {"app_id": app_id, "cases": len(cases), "patterns": len(patterns), "docs": len(docs)}},
    )

    return {"cases": cases, "patterns": patterns, "docs": docs}


async def stage2_retrieve(
    pipeline: RAGPipeline,
    initial_tc_text: str,
    app_id: str,
    stage1_doc_ids: list[str],
) -> dict[str, list[RAGResult]]:
    """Stage 2: Precise re-query with deduplication.

    Args:
        pipeline: The RAG pipeline instance.
        initial_tc_text: The refined query text (PRD + stage 1 context).
        app_id: The app identifier for filtering.
        stage1_doc_ids: Doc IDs from stage 1 to exclude from results.

    Returns:
        ``{"cases": [...], "patterns": [...], "docs": [...]}``
    """
    dedup_set = set(stage1_doc_ids)

    cases_task = _query_collection(
        pipeline, initial_tc_text, CASES_COLLECTION, top_k=5, app_id=app_id,
    )
    patterns_task = _query_collection(
        pipeline, initial_tc_text, PATTERNS_COLLECTION, top_k=3, app_id=app_id,
    )
    docs_task = _query_collection(
        pipeline, initial_tc_text, DOCS_COLLECTION, top_k=2, app_id=app_id,
    )

    raw_cases, raw_patterns, raw_docs = await asyncio.gather(cases_task, patterns_task, docs_task)

    deduped_cases = [r for r in raw_cases if r.doc_id not in dedup_set]
    deduped_patterns = [r for r in raw_patterns if r.doc_id not in dedup_set]
    deduped_docs = [r for r in raw_docs if r.doc_id not in dedup_set]

    logger.info(
        "Stage 2 retrieval for app '%s': %d cases (%d after dedup), %d patterns (%d after dedup), %d docs (%d after dedup)",
        app_id,
        len(raw_cases),
        len(deduped_cases),
        len(raw_patterns),
        len(deduped_patterns),
        len(raw_docs),
        len(deduped_docs),
        extra={
            "extra_data": {
                "app_id": app_id,
                "raw_cases": len(raw_cases),
                "deduped_cases": len(deduped_cases),
                "raw_patterns": len(raw_patterns),
                "deduped_patterns": len(deduped_patterns),
                "raw_docs": len(raw_docs),
                "deduped_docs": len(deduped_docs),
            }
        },
    )

    return {"cases": deduped_cases, "patterns": deduped_patterns, "docs": deduped_docs}


async def run_two_stage_retrieval(
    pipeline: RAGPipeline,
    prd_text: str,
    app_id: str,
) -> dict[str, Any]:
    """Orchestrate two-stage retrieval.

    Stage 1 performs a broad parallel search across cases, patterns, and docs.
    Stage 2 refines the query using stage 1 context and deduplicates.

    Args:
        pipeline: The RAG pipeline instance.
        prd_text: The PRD / requirement text.
        app_id: The app identifier for filtering.

    Returns:
        ``{"cases": [...], "patterns": [...], "docs": [...], "stage1_doc_ids": [...]}``
    """
    # ── Stage 1 ─────────────────────────────────────────────────────────────
    stage1 = await stage1_retrieve(pipeline, prd_text, app_id)

    stage1_doc_ids: list[str] = [
        r.doc_id for r in stage1["cases"]
    ] + [
        r.doc_id for r in stage1["patterns"]
    ] + [
        r.doc_id for r in stage1["docs"]
    ]

    # ── Stage 2 ─────────────────────────────────────────────────────────────
    stage2_query = _build_stage2_query(prd_text, stage1["cases"])
    stage2 = await stage2_retrieve(pipeline, stage2_query, app_id, stage1_doc_ids)

    # ── Combine ─────────────────────────────────────────────────────────────
    all_cases = stage1["cases"] + stage2["cases"]
    all_patterns = stage1["patterns"] + stage2["patterns"]
    all_docs = stage1["docs"] + stage2.get("docs", [])

    logger.info(
        "Two-stage retrieval complete for app '%s': %d total cases, %d total patterns, %d total docs",
        app_id,
        len(all_cases),
        len(all_patterns),
        len(all_docs),
        extra={
            "extra_data": {
                "app_id": app_id,
                "total_cases": len(all_cases),
                "total_patterns": len(all_patterns),
                "total_docs": len(all_docs),
                "stage1_doc_ids": stage1_doc_ids,
            }
        },
    )

    return {
        "cases": all_cases,
        "patterns": all_patterns,
        "docs": all_docs,
        "stage1_doc_ids": stage1_doc_ids,
    }
