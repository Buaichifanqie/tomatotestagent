"""App Context Memory — serialization and retrieval formatting.

Converts TestCase lists to searchable text for RAG storage,
and formats RAG retrieval results for prompt injection.
"""
from __future__ import annotations

import re

from testagent.plan.models import TestCase
from testagent.rag.pipeline import RAGResult


# ── Functional relevance filtering ──────────────────────────────────────────


def _extract_module_from_id(case_id: str) -> str | None:
    """TC-VIDEO-007 → VIDEO, TC-SEARCH-016 → SEARCH."""
    match = re.match(r"TC-(\w+)-\d+", case_id)
    return match.group(1) if match else None


def _extract_function_keywords(user_intent: str, app_package: str = "") -> list[str]:
    """Extract functional keywords from user intent.

    "测试搜索功能" → ["搜索"]
    "测试登录和支付" → ["登录", "支付"]
    "测试视频播放功能" → ["视频", "播放"]
    """
    # Strip common prefix
    cleaned = re.sub(r"^测试|^test|^验证", "", user_intent, flags=re.IGNORECASE).strip()
    # Strip "功能/feature/functionality" suffix
    cleaned = re.sub(r"功能$|feature$|functionality$", "", cleaned, flags=re.IGNORECASE).strip()
    # Strip generic app references
    cleaned = re.sub(r"该应用|此应用|这个应用|该App|此App|这个App|App的", "", cleaned).strip()

    # Strip app name from accounts config if available
    if app_package:
        try:
            from testagent.plan.app_accounts import get_login_config
            cfg = get_login_config(app_package)
            if cfg and cfg.get("name"):
                cleaned = cleaned.replace(cfg["name"], "").strip()
        except Exception:
            pass

    # Split on delimiters
    parts = re.split(r"[、，,和与及还有]+", cleaned)
    raw_keywords = [p.strip() for p in parts if p.strip()]

    # Match known keywords from compound text
    # e.g. "视频播放" → ["视频", "播放"]
    keywords: list[str] = []
    for part in raw_keywords:
        matched = False
        for kw in _KEYWORD_TO_MODULE:
            if kw in part:
                keywords.append(kw)
                matched = True
        if not matched and part:
            keywords.append(part)

    return keywords


def _is_functionally_relevant(
    intent_keywords: list[str],
    case_tags: list[str],
    case_module: str | None,
) -> bool:
    """Determine if a case is functionally relevant to the user intent."""
    if not case_module and not case_tags:
        return True
    if not intent_keywords:
        return True

    module_lower = (case_module or "").lower()

    for kw in intent_keywords:
        kw_lower = kw.lower()
        # Direct substring match (covers English ↔ English)
        if kw_lower in module_lower or module_lower in kw_lower:
            return True
        # Chinese keyword → English module mapping (substring match)
        for known_kw, mapped_module in _KEYWORD_TO_MODULE.items():
            if known_kw in kw_lower and mapped_module == module_lower:
                return True

    for tag in case_tags:
        for kw in intent_keywords:
            if kw.lower() in tag.lower() or tag.lower() in kw.lower():
                return True

    return False


# Chinese functional keyword → English module name mapping
# TODO: Phase 4 — auto-generate from historical case tags, replacing this hand-written table
_KEYWORD_TO_MODULE: dict[str, str] = {
    "搜索": "search",
    "登录": "login",
    "注册": "register",
    "支付": "pay",
    "视频": "video",
    "评论": "comment",
    "弹幕": "danmaku",
    "收藏": "favorite",
    "分享": "share",
    "点赞": "like",
    "播放": "video",
    "首页": "home",
    "设置": "setting",
    "消息": "message",
    "通知": "notification",
    "个人": "profile",
    "用户": "user",
    "订单": "order",
    "购物": "cart",
    "下载": "download",
    "上传": "upload",
    "权限": "permission",
    "网络": "network",
}


def filter_by_functional_relevance(
    results: list[RAGResult],
    user_intent: str,
    app_package: str = "",
) -> list[RAGResult]:
    """Filter out RAG results that are not functionally relevant to the user intent.

    Keeps:
    - Cases whose MODULE prefix matches an intent keyword (SEARCH ↔ 搜索)
    - Non-case results (patterns, docs) — always kept
    - Cases with no identifiable module — kept as fallback

    Removes:
    - Cases whose MODULE prefix clearly doesn't match (VIDEO ↔ 搜索)
    """
    if not results:
        return []

    intent_keywords = _extract_function_keywords(user_intent, app_package)
    if not intent_keywords:
        return results

    filtered: list[RAGResult] = []
    for r in results:
        # Non-case results (patterns, docs) are always kept
        if r.metadata.get("collection") != "app_test_cases":
            filtered.append(r)
            continue

        case_tags = r.metadata.get("tags", [])
        case_module = _extract_module_from_id(r.doc_id)

        if _is_functionally_relevant(intent_keywords, case_tags, case_module):
            filtered.append(r)

    return filtered


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

    lines: list[str] = ["以下是该 App 的历史测试用例（仅供参考，用于学习步骤写法和元素定位，不要照搬功能范围）：", ""]
    for i, r in enumerate(results, 1):
        score_pct = f"{r.score * 100:.0f}%"
        lines.append(f"--- 历史用例 {i}（相似度: {score_pct}）---")
        lines.append(r.content)
        lines.append("")

    return "\n".join(lines)


def format_doc_results_for_prompt(results: list[RAGResult]) -> str:
    """Format documentation RAG results into a prompt-ready context section."""
    if not results:
        return ""

    lines: list[str] = ["以下是该 App 的相关文档（仅供参考，用于了解 App 行为和 UI 元素）：", ""]
    for i, r in enumerate(results, 1):
        score_pct = f"{r.score * 100:.0f}%"
        doc_type = r.metadata.get("doc_type", "文档")
        lines.append(f"--- 文档 {i}（{doc_type}，相关度: {score_pct}）---")
        lines.append(r.content)
        lines.append("")

    return "\n".join(lines)


def format_learned_patterns_for_prompt(results: list[RAGResult]) -> str:
    """Format learned pattern RAG results into a prompt-ready context section."""
    if not results:
        return ""

    PATTERN_TYPE_LABELS = {
        "behavior": "行为模式",
        "workaround": "绕行方案",
        "anti_pattern": "反面模式",
        "failure_mode": "失败模式",
    }

    lines: list[str] = ["以下是该 App 的已学习测试模式（仅供参考，用于优化步骤写法）：", ""]
    for i, r in enumerate(results, 1):
        meta = r.metadata
        confidence = meta.get("confidence", 0.5)
        stars = "★" * round(confidence * 5)
        pattern_type = PATTERN_TYPE_LABELS.get(meta.get("pattern_type", ""), meta.get("pattern_type", ""))
        version = meta.get("app_version", "")

        lines.append(f"--- 经验 {i}（{pattern_type}，置信度: {stars}）---")
        lines.append(r.content)
        if version:
            lines.append(f"来源版本: {version}")
        lines.append("")

    return "\n".join(lines)
