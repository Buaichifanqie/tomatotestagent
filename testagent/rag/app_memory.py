"""App Context Memory — serialization and retrieval formatting.

Converts TestCase lists to searchable text for RAG storage,
and formats RAG retrieval results for prompt injection.
"""
from __future__ import annotations

from testagent.plan.models import TestCase
from testagent.rag.pipeline import RAGResult


def serialize_cases_for_storage(cases: list[TestCase]) -> str:
    """Serialize a list of TestCase objects into searchable plain text.

    Output format is human-readable text (not JSON) optimized for RAG chunking
    and semantic retrieval. Each case becomes a structured block.
    """
    if not cases:
        return ""

    blocks: list[str] = []
    for tc in cases:
        lines: list[str] = []
        lines.append(f"用例: {tc.id} {tc.title}")
        lines.append(f"优先级: {tc.priority}")
        if tc.is_core:
            lines.append("核心用例: 是")
        if tc.requirement_ids:
            lines.append(f"关联需求: {', '.join(tc.requirement_ids)}")
        if tc.steps:
            step_lines: list[str] = []
            for s in tc.steps:
                parts = [f"{s.step}. [{s.action}]"]
                if s.target:
                    parts.append(f"target={s.target}")
                if s.value:
                    parts.append(f"value={s.value}")
                step_lines.append(" ".join(parts))
            lines.append("步骤:\n" + "\n".join(step_lines))
        blocks.append("\n".join(lines))

    return "\n\n---\n\n".join(blocks)


def format_retrieved_cases_for_prompt(results: list[RAGResult]) -> str:
    """Format RAG retrieval results into a prompt-ready context section.

    Returns a formatted string suitable for prepending to the TC generation prompt.
    Returns empty string if no results.
    """
    if not results:
        return ""

    lines: list[str] = ["以下是该 App 的历史测试用例（仅供参考，避免重复）：", ""]
    for i, r in enumerate(results, 1):
        score_pct = f"{r.score * 100:.0f}%"
        lines.append(f"--- 历史用例 {i}（相似度: {score_pct}）---")
        lines.append(r.content)
        lines.append("")

    return "\n".join(lines)
