"""Actor-Critic 交叉校验模块。

模型 A（Actor）生成测试用例后，
模型 B（Critic）校验是否有场景遗漏，
自动补偿缺失场景。
"""
from __future__ import annotations

import json
import re
from typing import Any

from testagent.plan.models import TestCase


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
        '请以 JSON 格式返回：{"missing_scenarios":["场景1描述","场景2描述",...],"redundant":["TC-ID-1",...]}\n'
        "如果没有缺失场景，missing_scenarios 返回空数组。"
    )
    try:
        response = await llm_callable(prompt)
        match = re.search(r"\{.*\}", response, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return data.get("missing_scenarios", [])
    except Exception:
        pass
    return []


async def generate_supplementary(
    prd_text: str,
    existing_cases: list[TestCase],
    missing_scenarios: list[str],
    llm_callable: Any,
) -> list[TestCase]:
    """为缺失场景补充生成测试用例。

    Args:
        prd_text: 原始需求文本。
        existing_cases: 已有的用例列表（用于编号和去重）。
        missing_scenarios: _cross_validate 返回的缺失场景列表。
        llm_callable: async (prompt) -> str 的 LLM 调用函数。

    Returns:
        补充生成的 TestCase 列表。
    """
    from testagent.plan.test_case_generator import _extract_json, _dict_to_tc

    existing_list = "\n".join(f"  {tc.id}: {tc.title}" for tc in existing_cases)
    scenarios = "\n".join(f"  {i+1}. {s}" for i, s in enumerate(missing_scenarios))
    prompt = (
        f"## 原始需求\n\n{prd_text[:3000]}\n\n"
        f"## 已有用例\n\n{existing_list}\n\n"
        f"## 缺失场景\n\n请为以下缺失场景各生成 1-2 条测试用例：\n\n{scenarios}\n\n"
        "请以 JSON 数组格式输出，格式与标准测试用例一致：\n"
        '[{"id": "TC-SUP-001", "title": "...", "priority": "P1", "steps": [...]}, ...]'
    )
    try:
        response = await llm_callable(prompt)
        data = _extract_json(response)
        if data and isinstance(data, list):
            cases: list[TestCase] = []
            nums = []
            for tc in existing_cases:
                m = re.search(r"(\d+)$", tc.id.split("-")[-1])
                if m:
                    nums.append(int(m.group(1)))
            next_num = max(nums) + 1 if nums else len(existing_cases) + 1
            for item in data:
                if isinstance(item, dict):
                    item["id"] = item.get("id", f"TC-SUP-{next_num:03d}")
                    next_num += 1
                    try:
                        tc = _dict_to_tc(item)
                        if tc:
                            cases.append(tc)
                    except Exception:
                        continue
            return cases
    except Exception:
        pass
    return []
