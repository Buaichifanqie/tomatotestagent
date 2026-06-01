"""Delta detection when users edit test cases.

Provides LLM-based extraction of experience patterns from test case modifications,
with a 3-button confirmation UI for saving, editing, or ignoring detected changes.
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine

import typer

from testagent.common.logging import get_logger
from testagent.plan.models import TestCase, TestStep

logger = get_logger(__name__)


def _edit_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein distance between two strings."""
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)

    rows = len(s1) + 1
    cols = len(s2) + 1
    dp = [[0] * cols for _ in range(rows)]

    for i in range(rows):
        dp[i][0] = i
    for j in range(cols):
        dp[0][j] = j

    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,       # deletion
                dp[i][j - 1] + 1,       # insertion
                dp[i - 1][j - 1] + cost, # substitution
            )

    return dp[rows - 1][cols - 1]


def _is_meaningful_target_change(old_target: str, new_target: str) -> bool:
    """Determine if a target change is meaningful (not a typo)."""
    if not old_target and not new_target:
        return False
    if not old_target or not new_target:
        return True

    ed = _edit_distance(old_target, new_target)
    len_diff = abs(len(old_target) - len(new_target))

    if ed <= 2 and len_diff <= 2:
        return False  # likely a typo
    return True


def _has_meaningful_step_change(old_steps: list[dict[str, Any]], new_steps: list[dict[str, Any]]) -> bool:
    """Check if steps have meaningful changes: count, action, target, or order."""
    if len(old_steps) != len(new_steps):
        return True

    for old, new in zip(old_steps, new_steps, strict=False):
        if old.get("action") != new.get("action"):
            return True
        if _is_meaningful_target_change(old.get("target", ""), new.get("target", "")):
            return True

    # Check step order: same (action, target) pairs but different ordering
    old_sig = [(s.get("action", ""), s.get("target", "")) for s in old_steps]
    new_sig = [(s.get("action", ""), s.get("target", "")) for s in new_steps]
    if sorted(old_sig) == sorted(new_sig) and old_sig != new_sig:
        return True

    return False


def should_trigger_extraction(
    original: dict[str, list[dict[str, Any]]],
    modified: list[TestCase],
) -> bool:
    """Check if any TC has meaningful changes that warrant extraction."""
    for tc in modified:
        old_steps = original.get(tc.id)
        if old_steps is None:
            continue
        new_steps = [s.model_dump() for s in tc.steps]
        if _has_meaningful_step_change(old_steps, new_steps):
            return True
    return False


async def extract_delta_summary(
    original_steps: dict[str, list[dict[str, Any]]],
    modified_cases: list[TestCase],
    llm_callable: Callable[[str], Coroutine[Any, Any, str]],
) -> list[dict[str, Any]]:
    """Extract delta summaries with LLM-generated experience descriptions.

    Returns list of dicts with keys: case_id, old_steps, new_steps, experience.
    """
    deltas: list[dict[str, Any]] = []

    for tc in modified_cases:
        old_steps = original_steps.get(tc.id)
        if old_steps is None:
            continue
        new_steps = [s.model_dump() for s in tc.steps]
        if not _has_meaningful_step_change(old_steps, new_steps):
            continue

        prompt = _build_extraction_prompt(tc.id, old_steps, new_steps)
        experience = await llm_callable(prompt)

        deltas.append({
            "case_id": tc.id,
            "old_steps": old_steps,
            "new_steps": new_steps,
            "experience": experience,
        })

    return deltas


def _build_extraction_prompt(
    tc_id: str,
    old_steps: list[dict[str, Any]],
    new_steps: list[dict[str, Any]],
) -> str:
    """Build a prompt for LLM to extract experience from step changes."""
    old_desc = "\n".join(
        f"  Step {s.get('step')}: {s.get('action')} -> {s.get('target', '')} "
        f"(value: {s.get('value', '')})"
        for s in old_steps
    )
    new_desc = "\n".join(
        f"  Step {s.get('step')}: {s.get('action')} -> {s.get('target', '')} "
        f"(value: {s.get('value', '')})"
        for s in new_steps
    )

    return (
        f"用户修改了测试用例 {tc_id} 的步骤:\n\n"
        f"原始步骤:\n{old_desc}\n\n"
        f"修改后步骤:\n{new_desc}\n\n"
        f"请用一句话总结从这次修改中可以学到的用户体验或操作经验。"
        f"只输出经验描述，不要输出其他内容。"
    )


async def process_deltas_and_confirm(
    test_cases: list[TestCase],
    original_steps: dict[str, list[dict[str, Any]]],
    app_id: str,
    plan_name: str,
    llm_callable: Callable[[str], Coroutine[Any, Any, str]],
    rag_pipeline: Any,
    pattern_repo: Any = None,
) -> None:
    """Detect deltas, extract experience, and show 3-button confirmation UI."""
    if not should_trigger_extraction(original_steps, test_cases):
        logger.info("No meaningful changes detected, skipping extraction")
        return

    deltas = await extract_delta_summary(original_steps, test_cases, llm_callable)

    for delta in deltas:
        case_id = delta["case_id"]
        experience = delta["experience"]

        print(f"\n检测到用例 {case_id} 的变更:")
        print(f"  LLM 提取的经验: {experience}")

        choice = typer.prompt(
            "  [s] 保存  [e] 修改后保存  [i] 忽略",
            type=str,
        )

        if choice == "i":
            logger.info("User ignored delta for %s", case_id)
            continue

        if choice == "e":
            experience = typer.prompt("请输入修改后的经验描述", type=str)

        # Build pattern content
        pattern_content = (
            f"App: {app_id}\n"
            f"Plan: {plan_name}\n"
            f"Case: {case_id}\n"
            f"Experience: {experience}\n"
            f"Old steps: {delta['old_steps']}\n"
            f"New steps: {delta['new_steps']}"
        )

        # Dedup check: query existing patterns
        is_duplicate = False
        if pattern_repo is not None:
            is_duplicate = await _check_and_increment_duplicate(
                rag_pipeline, pattern_repo, pattern_content, app_id, case_id
            )

        if not is_duplicate:
            # Write to RAG
            await rag_pipeline.write_back(
                content=pattern_content,
                collection="app_learned_patterns",
                metadata={
                    "app_id": app_id,
                    "plan_name": plan_name,
                    "case_id": case_id,
                    "pattern_type": "behavior",
                    "source_type": "modification_delta",
                },
                chunk_size=256,
            )
            logger.info("Saved pattern for %s to RAG", case_id)

            # Write to SQLite if repo available
            if pattern_repo is not None:
                try:
                    from testagent.models.learned_pattern import LearnedPattern

                    pattern = LearnedPattern(
                        app_id=app_id,
                        pattern=experience,
                        pattern_type="behavior",
                        source_case_id=case_id,
                        source_type="modification_delta",
                        confidence=0.7,
                        scope="app_local",
                        review_status="pending",
                    )
                    await pattern_repo.create(pattern)
                    logger.info("Saved pattern for %s to SQLite", case_id)
                except Exception as exc:
                    logger.warning("Failed to save pattern to SQLite: %s", exc)


async def _check_and_increment_duplicate(
    rag_pipeline: Any,
    pattern_repo: Any,
    pattern_content: str,
    app_id: str,
    case_id: str,
) -> bool:
    """Check for duplicate patterns and increment occurrence_count if found."""
    try:
        results = await rag_pipeline.query(
            query_text=pattern_content,
            collection="app_learned_patterns",
            top_k=1,
        )
        if results and results[0].score > 0.9:
            existing_id = results[0].metadata.get("pattern_id")
            if existing_id:
                existing = await pattern_repo.get_by_id(existing_id)
                if existing is not None:
                    await pattern_repo.update(
                        existing_id,
                        {"occurrence_count": existing.occurrence_count + 1},
                    )
                    logger.info(
                        "Incremented occurrence_count for existing pattern %s", existing_id
                    )
                    return True
    except Exception as exc:
        logger.warning("Dedup check failed: %s", exc)

    return False
