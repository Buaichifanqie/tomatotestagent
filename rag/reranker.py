from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

from testagent.common.logging import get_logger

logger = get_logger(__name__)


@runtime_checkable
class IReranker(Protocol):
    async def rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        top_k: int = 5,
    ) -> list[dict[str, Any]]: ...


class CrossEncoderReranker:
    """Cross-Encoder reranker (V1.0).

    Scores query-document pairs via Cross-Encoder model,
    sorts by score descending, returns top_k results.
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-large",
    ) -> None:
        self._model_name = model_name
        self._model: Any | None = None

    def _ensure_model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import CrossEncoder
            except ImportError as err:
                raise ImportError(
                    "sentence-transformers is required for CrossEncoderReranker. "
                    "Install it with: pip install sentence-transformers"
                ) from err
            self._model = CrossEncoder(self._model_name)
            logger.info(
                "Loaded Cross-Encoder model: %s",
                self._model_name,
                extra={
                    "extra_data": {
                        "model_name": self._model_name,
                    }
                },
            )
        return self._model

    async def rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        if not documents:
            return []

        model = await asyncio.get_event_loop().run_in_executor(
            None, self._ensure_model
        )

        pairs: list[list[str]] = []
        for doc in documents:
            doc_text = str(doc.get("document", doc.get("content", "")))
            pairs.append([query, doc_text])

        scores: list[float] = await asyncio.get_event_loop().run_in_executor(
            None, model.predict, pairs
        )

        scored_docs: list[tuple[float, dict[str, Any]]] = []
        for score, doc in zip(scores, documents, strict=True):
            doc_copy = dict(doc)
            doc_copy["rerank_score"] = float(score)
            scored_docs.append((float(score), doc_copy))

        scored_docs.sort(key=lambda x: x[0], reverse=True)

        result = [doc for _, doc in scored_docs[:top_k]]

        logger.debug(
            "Reranked %d documents, returned top %d",
            len(documents),
            len(result),
            extra={
                "extra_data": {
                    "input_count": len(documents),
                    "output_count": len(result),
                    "model_name": self._model_name,
                }
            },
        )
        return result


class NoopReranker:
    """Noop reranker (MVP compatible).

    Returns the first top_k documents without reranking.
    """

    async def rerank(
        self,
        query: str,
        documents: list[dict[str, Any]],
        top_k: int = 5,
    ) -> list[dict[str, Any]]:
        return documents[:top_k]


class RerankerFactory:
    """Reranker factory, creates the appropriate implementation based on config."""

    @staticmethod
    def create(reranker_enabled: bool = False, reranker_model: str = "BAAI/bge-reranker-large") -> IReranker:
        if reranker_enabled:
            logger.info(
                "Creating CrossEncoderReranker with model: %s",
                reranker_model,
                extra={
                    "extra_data": {
                        "reranker_enabled": True,
                        "model_name": reranker_model,
                    }
                },
            )
            return CrossEncoderReranker(model_name=reranker_model)

        logger.info(
            "Reranker disabled, using NoopReranker",
            extra={"extra_data": {"reranker_enabled": False}},
        )
        return NoopReranker()
