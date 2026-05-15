from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from testagent.common.errors import TestAgentError
from testagent.common.logging import get_logger

if TYPE_CHECKING:
    from testagent.db.repository import DefectRepository
    from testagent.llm.base import ILLMProvider
    from testagent.rag.pipeline import RAGPipeline

logger = get_logger(__name__)

DEFECT_HISTORY_COLLECTION = "defect_history"
DUPLICATE_SIMILARITY_THRESHOLD = 0.85


class DefectDeduplicationError(TestAgentError):
    pass


@dataclass
class DeduplicationResult:
    is_duplicate: bool
    similarity_score: float
    original_defect_id: str | None
    similar_defects: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_duplicate": self.is_duplicate,
            "similarity_score": self.similarity_score,
            "original_defect_id": self.original_defect_id,
            "similar_defects": self.similar_defects,
        }


def _get_defect_field(defect: Any, field: str, default: Any = "") -> Any:
    if isinstance(defect, dict):
        return defect.get(field, default)
    return getattr(defect, field, default)


def _get_defect_id(defect: Any) -> str:
    if isinstance(defect, dict):
        raw_id: object = defect.get("id", "")
        return "" if raw_id is None else str(raw_id)
    raw_attr: object = getattr(defect, "id", "")
    return "" if raw_attr is None else str(raw_attr)


class DefectDeduplicator:
    def __init__(
        self,
        llm: ILLMProvider,
        rag: RAGPipeline,
        defect_repo: DefectRepository,
    ) -> None:
        self._llm = llm
        self._rag = rag
        self._defect_repo = defect_repo

    async def check_duplicate(self, defect: Any) -> DeduplicationResult:
        query_text = self._build_search_query(defect)

        similar_defects = await self._query_similar_defects(query_text)

        if not similar_defects:
            logger.info(
                "No similar defects found in RAG, defect is unique",
                extra={"extra_data": {"defect_title": _get_defect_field(defect, "title", "")[:80]}},
            )
            return DeduplicationResult(
                is_duplicate=False,
                similarity_score=0.0,
                original_defect_id=None,
                similar_defects=[],
            )

        max_score = 0.0
        best_match_id: str | None = None
        enriched_similar: list[dict[str, Any]] = []

        for similar in similar_defects[:5]:
            score = await self._llm_judge_similarity(
                new_defect=defect,
                existing_defect=similar,
            )

            similar_entry = {
                "doc_id": similar.get("doc_id", ""),
                "defect_id": similar.get("metadata", {}).get("defect_id", ""),
                "title": similar.get("metadata", {}).get("defect_title", ""),
                "content": similar.get("content", "")[:300],
                "similarity": score,
            }
            enriched_similar.append(similar_entry)

            if score > max_score:
                max_score = score
                best_match_id = similar.get("metadata", {}).get("defect_id", "")

        is_duplicate = max_score >= DUPLICATE_SIMILARITY_THRESHOLD

        if is_duplicate and best_match_id:
            await self._increment_occurrence(best_match_id)

        logger.info(
            "Defect deduplication %s",
            "duplicate found" if is_duplicate else "unique defect",
            extra={
                "extra_data": {
                    "defect_title": _get_defect_field(defect, "title", "")[:80],
                    "is_duplicate": is_duplicate,
                    "similarity_score": round(max_score, 4),
                    "original_defect_id": best_match_id,
                }
            },
        )

        return DeduplicationResult(
            is_duplicate=is_duplicate,
            similarity_score=max_score,
            original_defect_id=best_match_id,
            similar_defects=enriched_similar,
        )

    def _build_search_query(self, defect: Any) -> str:
        title = _get_defect_field(defect, "title", "")
        description = _get_defect_field(defect, "description", "")
        category = _get_defect_field(defect, "category", "")
        severity = _get_defect_field(defect, "severity", "")

        parts: list[str] = []
        if title:
            parts.append(f"title: {title}")
        if description:
            parts.append(f"description: {description}")
        if category:
            parts.append(f"category: {category}")
        if severity:
            parts.append(f"severity: {severity}")

        query = " | ".join(parts)
        return query[:2000]

    async def _query_similar_defects(self, query_text: str) -> list[dict[str, Any]]:
        try:
            results = await self._rag.query(
                query_text=query_text,
                collection=DEFECT_HISTORY_COLLECTION,
                top_k=5,
            )
            return [
                {
                    "doc_id": r.doc_id,
                    "content": r.content,
                    "score": r.score,
                    "metadata": r.metadata,
                }
                for r in results
            ]
        except Exception as exc:
            logger.warning(
                "Failed to query similar defects from RAG defect_history",
                extra={"extra_data": {"error": str(exc)}},
            )
            return []

    async def _llm_judge_similarity(
        self,
        new_defect: Any,
        existing_defect: dict[str, Any],
    ) -> float:
        new_title = _get_defect_field(new_defect, "title", "")
        new_desc = _get_defect_field(new_defect, "description", "")
        new_category = _get_defect_field(new_defect, "category", "")
        new_severity = _get_defect_field(new_defect, "severity", "")

        existing_content = existing_defect.get("content", "")
        existing_meta = existing_defect.get("metadata", {})
        existing_title = existing_meta.get("defect_title", "")
        existing_category = existing_meta.get("defect_category", "")
        existing_severity = existing_meta.get("defect_severity", "")

        system_prompt = """You are a defect deduplication expert. Determine whether two defect
reports describe the same underlying issue.

Compare the following two defects and return a JSON object with a similarity score
between 0.0 (completely different) and 1.0 (exactly the same).

Consider these factors:
- Same error message / exception type
- Same component or module
- Same root cause
- Same title/description semantics
- Different test case but same underlying issue

A score >= 0.85 means they are definitely the same issue (duplicate).
A score between 0.5 and 0.84 means they are related but not the same.
A score < 0.5 means they are different issues.

Respond ONLY with a JSON object:
{
  "similarity_score": 0.0-1.0,
  "reasoning": "brief explanation of the similarity judgment"
}"""

        user_prompt = f"""--- New Defect ---
Title: {new_title}
Category: {new_category}
Severity: {new_severity}
Description: {new_desc[:1000]}

--- Existing Defect (from history) ---
Title: {existing_title}
Category: {existing_category}
Severity: {existing_severity}
Content: {existing_content[:1000]}

Is this new defect a duplicate of the existing defect?"""

        try:
            response = await self._llm.chat(
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=256,
                temperature=0.1,
            )

            for block in response.content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = str(block.get("text", ""))
                    try:
                        parsed = json.loads(text)
                        if isinstance(parsed, dict):
                            score = float(parsed.get("similarity_score", 0.0))
                            return max(0.0, min(1.0, score))
                    except (json.JSONDecodeError, ValueError):
                        import re

                        json_match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
                        if json_match:
                            try:
                                parsed = json.loads(json_match.group())
                                if isinstance(parsed, dict):
                                    score = float(parsed.get("similarity_score", 0.0))
                                    return max(0.0, min(1.0, score))
                            except (json.JSONDecodeError, ValueError):
                                pass
                        logger.warning(
                            "Failed to parse LLM similarity response as JSON, defaulting to 0.0",
                            extra={"extra_data": {"text": text[:200]}},
                        )
                        return 0.0

        except Exception as exc:
            logger.warning(
                "LLM similarity judgment failed, defaulting to 0.0",
                extra={"extra_data": {"error": str(exc)}},
            )

        return 0.0

    async def _increment_occurrence(self, defect_id: str) -> None:
        try:
            defect = await self._defect_repo.get_by_id(defect_id)
            if defect is None:
                logger.warning(
                    "Original defect not found for occurrence increment",
                    extra={"extra_data": {"defect_id": defect_id}},
                )
                return

            current_count = getattr(defect, "occurrence_count", 1)
            await self._defect_repo.update(
                defect_id,
                {"occurrence_count": current_count + 1},
            )

            logger.info(
                "Incremented occurrence_count for original defect",
                extra={
                    "extra_data": {
                        "defect_id": defect_id,
                        "new_count": current_count + 1,
                    }
                },
            )
        except Exception as exc:
            logger.warning(
                "Failed to increment occurrence_count for original defect",
                extra={"extra_data": {"defect_id": defect_id, "error": str(exc)}},
            )

    async def write_back_to_rag(
        self,
        defect: Any,
        dedup_result: DeduplicationResult,
    ) -> None:
        defect_id = _get_defect_id(defect)
        try:
            write_back_content = json.dumps(
                {
                    "defect_id": defect_id,
                    "defect_title": _get_defect_field(defect, "title", ""),
                    "defect_category": _get_defect_field(defect, "category", "unknown"),
                    "defect_severity": _get_defect_field(defect, "severity", "minor"),
                    "defect_description": _get_defect_field(defect, "description", "")[:500],
                    "is_duplicate": dedup_result.is_duplicate,
                    "similarity_score": dedup_result.similarity_score,
                    "original_defect_id": dedup_result.original_defect_id,
                },
                ensure_ascii=False,
                default=str,
            )

            metadata: dict[str, Any] = {
                "defect_id": defect_id,
                "defect_title": _get_defect_field(defect, "title", ""),
                "defect_category": _get_defect_field(defect, "category", "unknown"),
                "defect_severity": _get_defect_field(defect, "severity", "minor"),
                "is_duplicate": dedup_result.is_duplicate,
                "similarity_score": dedup_result.similarity_score,
                "original_defect_id": dedup_result.original_defect_id or "",
                "analyzed_at": datetime.now(UTC).isoformat(),
            }

            await self._rag.write_back(
                content=write_back_content,
                collection=DEFECT_HISTORY_COLLECTION,
                metadata=metadata,
            )

            logger.info(
                "Defect deduplication result written back to RAG defect_history",
                extra={
                    "extra_data": {
                        "defect_id": defect_id,
                        "is_duplicate": dedup_result.is_duplicate,
                        "collection": DEFECT_HISTORY_COLLECTION,
                    }
                },
            )
        except Exception as exc:
            logger.warning(
                "Failed to write back deduplication result to RAG",
                extra={"extra_data": {"defect_id": defect_id, "error": str(exc)}},
            )
