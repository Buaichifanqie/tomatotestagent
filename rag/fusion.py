from __future__ import annotations

from typing import Any


def rrf_fusion(
    vector_results: list[dict[str, Any]],
    keyword_results: list[dict[str, Any]],
    k: int = 60,
) -> list[dict[str, Any]]:
    """
    RRF 融合排序:
    score = Sigma 1/(k + rank_i)  对每路结果按排名赋分
    合并同 doc_id 分数
    按 final_score 降序排序
    """
    scores: dict[str, float] = {}
    merged: dict[str, dict[str, Any]] = {}

    for rank, item in enumerate(vector_results):
        doc_id = str(item["id"])
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
        if doc_id not in merged:
            merged[doc_id] = dict(item)

    for rank, item in enumerate(keyword_results):
        doc_id = str(item["id"])
        scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank + 1)
        if doc_id not in merged:
            merged[doc_id] = dict(item)

    sorted_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)

    result: list[dict[str, Any]] = []
    for doc_id in sorted_ids:
        entry = merged[doc_id]
        entry["score"] = scores[doc_id]
        result.append(entry)

    return result
