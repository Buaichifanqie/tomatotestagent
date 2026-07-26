"""Actor-Critic 交叉校验模块。

模型 A（Actor）生成测试用例后，
模型 B（Critic）校验是否有场景遗漏，
自动补偿缺失场景。
"""
from __future__ import annotations

import json
import re
from typing import Any

from testagent.plan.models import TestCase, TestStep


async def cross_validate(
    prd_text: str,
    generated_cases: list[TestCase],
    llm_callable: Any,
) -> list[str]:
    """模型 B 校验：检查已生成的用例是否有场景遗漏。

    Args:
        prd_text: 原始需求文本。
        generated_cases: 模型 A 已生成的用例列表。
        llm_callable: async (prompt) -> str 的 LLM 调用函数。

    Returns:
        缺失场景描述列表（空列表表示无遗漏）。
    """
    case_list = "\n".join(
        f"  {tc.id}: {tc.title} [{tc.priority}]" for tc in generated_cases
    )
    prompt = (
        f"## 原始需求\n\n{prd_text[:4000]}\n\n"
        f"## 已生成的测试用例\n\n{case_list}\n\n"
        "## 任务\n\n"
        "你是一个严谨的测试架构师。请对比【原始需求】和【已生成的用例列表】：\n"
        "1. 完整性检查：列出原始需求中存在、但用例列表中遗漏的场景。\n"
        "2. 冗余检查：列出语义重复的用例。\n\n"
        '请以 JSON 格式返回：{"missing_scenarios":["场景1","场景2",...],"redundant":["TC-ID-1",...]}\n'
        "如果没有缺失场景，missing_scenarios 返回空数组。"
    )
    try:
        response = await llm_callable(prompt)
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return data.get("missing_scenarios", [])
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Critic call failed: {e}")
    return []


async def generate_supplementary(
    prd_text: str,
    existing_cases: list[TestCase],
    missing_scenarios: list[str],
    llm_callable: Any,
) -> list[TestCase]:
    """为缺失场景补充生成测试用例。"""
    existing_list = "\n".join(f"  {tc.id}: {tc.title}" for tc in existing_cases)
    scenarios = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(missing_scenarios))
    prompt = (
        f"## 原始需求\n\n{prd_text[:3000]}\n\n"
        f"## 已有用例\n\n{existing_list}\n\n"
        f"## 缺失场景\n\n请为以下缺失场景各生成 1-2 条测试用例：\n\n{scenarios}\n\n"
        "请以 JSON 数组格式输出：\n"
        '[{"id": "TC-SUP-001", "title": "...", "priority": "P1", "steps": [{"step": 1, "action": "tap", "target": "..."}, ...]}, ...]'
    )
    try:
        response = await llm_callable(prompt)
        data = _extract_json_array(response)
        if data and isinstance(data, list):
            cases: list[TestCase] = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                try:
                    tc = _dict_to_tc(item, existing_cases)
                    if tc:
                        cases.append(tc)
                except Exception:
                    continue
            return cases
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Supplementary generation failed: {e}")
    return []


# ── Internal helpers (module-level, not class methods) ─────────────


def _extract_json_array(raw: str) -> list | None:
    """Extract a JSON array from raw text (handles markdown fences)."""
    # Try markdown code block first
    match = re.search(r"```(?:json)?\s*\n?(\[[\s\S]*?\])\n?\s*```", raw)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # Try raw JSON array
    match = re.search(r"(\[[\s\S]*?\])", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    return None


def _dict_to_tc(item: dict, existing: list[TestCase] | None = None) -> TestCase | None:
    """Convert a dict to TestCase (mirrors the instance method)."""
    steps_raw = item.get("steps", [])
    steps: list[TestStep] = []
    for i, s in enumerate(steps_raw):
        if isinstance(s, dict):
            steps.append(TestStep(
                step=s.get("step", i + 1),
                action=s.get("action", "tap"),
                target=s.get("target", ""),
                value=s.get("value", ""),
                expected=s.get("expected", ""),
            ))

    # Generate unique ID
    tc_id = item.get("id", "")
    if not tc_id and existing:
        nums = []
        for tc in existing:
            m = re.search(r"(\d+)$", tc.id.split("-")[-1])
            if m:
                nums.append(int(m.group(1)))
        next_num = max(nums) + 1 if nums else len(existing) + 1
        tc_id = f"TC-SUP-{next_num:03d}"

    if not tc_id:
        return None

    return TestCase(
        id=tc_id,
        title=item.get("title", ""),
        priority=item.get("priority", "P2"),
        steps=steps,
    )
