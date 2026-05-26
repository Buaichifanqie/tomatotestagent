from __future__ import annotations

import json
import re
from typing import Any

from testagent.plan.models import TestCase, TestStep

# ── 测试用例生成提示词 ───────────────────────────────────────────
TC_GENERATION_SYSTEM_PROMPT = (
    "你是一名拥有十年测试开发经验的高级测试开发工程师，现需为 Android 移动 App 生成全面的测试用例。\n"
    "你的目标是生成**足够多、足够全面**的测试用例，不要只覆盖基本流程。\n"
    "\n"
    "请根据文档描述的功能点，全面覆盖以下六类场景：\n"
    "\n"
    "1. **功能正常流程** — 核心业务路径、最常用的用户操作路径、端到端操作链\n"
    "2. **功能异常操作** — 输入校验、空值/空状态处理、重复操作、流程中断\n"
    "3. **边界条件** — 等价类边界、最大/最小输入、特殊字符、格式边界检查\n"
    "4. **权限/登录状态** — 未登录访问受限功能、不同登录状态下的操作差异\n"
    "5. **异常场景** — 断网、弱网、切换后台、频繁操作等稳定性场景\n"
    "6. **业务流程** — 覆盖核心功能的端到端组合流程\n"
    "\n"
    "## 优先级定义\n"
    "\n"
    "- **P0** — 核心功能，阻塞性问题，约占总量 10-15%\n"
    "- **P1** — 重要功能，非阻塞但有影响，约占 30-40%\n"
    "- **P2** — 边界情况、用户体验细节\n"
    "- **P3** — 罕见边界场景\n"
    "\n"
    "## 命名规范\n"
    "\n"
    '用例 ID 格式为 `TC-{MODULE}-{NUM}`，例如 `TC-SEARCH-001`、`TC-LOGIN-002`。\n'
    "用例名称必须清晰表达测试意图。\n"
    "\n"
    "## 允许的操作类型\n"
    "\n"
    '每个步骤的 `action` 字段必须是以下之一：\n'
    '\n'
    '- `"tap"` — 点击 UI 元素。所有点击操作都用此类型\n'
    '- `"type"` — 在输入框中输入文字。`value` 字段填写要输入的文字\n'
    '- `"launch"` — 通过包名启动 Android App。`target` 填包名\n'
    '- `"swipe"` — 滑动手势。`target` 格式为 "start_x,start_y,end_x,end_y"\n'
    '- `"assert"` — 断言验证 UI 元素可见。`target` 必须是屏幕上实际可见的短文本标签，如 "推荐"、"热门"、"关注"、"我的"\n'
    '- `"exec"` — 执行 Android shell 命令，用于设备级操作如开关 WiFi、清除数据等\n'
    '- `"screenshot"` — 截图用于视觉校验\n'
    "\n"
    "## 平台约束\n"
    "\n"
    "- 这是 **Android 移动 App**，不是 Web 浏览器\n"
    "- 禁止使用 Web 概念如 navigate to URL、cookie、viewport 等\n"
    "- 所有操作必须是移动端 UI 交互：点击、输入、滑动、长按等\n"
    "- 使用 `exec` 进行设备级操作（如切换网络）\n"
    "\n"
    "## 输出格式\n"
    "\n"
    "只输出一个合法的 JSON 数组，不包含任何 markdown 标记、代码块或解释文字。\n"
    "\n"
    "每个用例对象格式：\n"
    "```json\n"
    "{\n"
    '  "id": "TC-MODULE-001",\n'
    '  "title": "简洁的用例名称",\n'
    '  "priority": "P0",\n'
    '  "is_core": true,\n'
    "  \"steps\": [\n"
    '    {"step": 1, "action": "launch", "target": "tv.danmaku.bili", "value": ""},\n'
    '    {"step": 2, "action": "assert", "target": "推荐", "value": ""},\n'
    '    {"step": 3, "action": "screenshot", "target": "", "value": ""}\n'
    "  ]\n"
    "}\n"
    "```\n"
    "\n"
    "## 覆盖率要求（重点关注）\n"
    "\n"
    "- **每个功能点必须覆盖多种场景**：正常流程、异常操作、边界条件、不同登录状态\n"
    "- **异常场景不可遗漏**：断网、弱网、空数据、快速频繁操作、切换后台\n"
    "- **业务流程不可遗漏**：覆盖从启动到核心操作的端到端流程\n"
    "- 不要编造文档中不存在的功能需求\n"
    "- 不要生成重复的测试用例\n"
)


class TestCaseGenerator:
    """Generates structured test cases from PRD text using an LLM or fallback."""

    def __init__(self, llm_provider: Any = None) -> None:
        self._llm_provider = llm_provider
        self.last_raw_output: str = ""  # populated after each generate() call

    # ── public API ───────────────────────────────────────────────────────────

    def generate(
        self, prd_text: str, plan_name: str = ""
    ) -> list[TestCase]:
        if self._llm_provider is not None:
            raw = self._call_llm(prd_text)
        else:
            raw = prd_text
        self.last_raw_output = raw
        result = self._parse_response(raw)
        if not result:
            lines = raw.strip().split("\n")
            preview = "\n".join(lines[:10])
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(
                "TC generation parsing failed. Raw output preview:\n%s",
                preview,
            )
        return result

    # ── LLM helpers ──────────────────────────────────────────────────────────

    def _call_llm(self, prd_text: str) -> str:
        """Call the LLM provider and return the raw response text."""
        return self._llm_provider(prd_text)  # type: ignore[misc]

    # ── response parsing ─────────────────────────────────────────────────────

    def _parse_response(self, raw: str) -> list[TestCase]:
        """Parse the LLM response into a list of TestCase objects."""
        extracted = self._extract_json(raw)

        # ── Try 1: full JSON array from extracted block ──────────────
        if extracted:
            cleaned = re.sub(r",\s*([\]}])", r"\1", extracted)
            try:
                items = json.loads(cleaned)
                if isinstance(items, list):
                    return [self._dict_to_tc(item) for item in items if isinstance(item, dict)]
            except json.JSONDecodeError:
                pass

            # Try fallback parse (individual objects) on extracted block
            items = self._fallback_parse(extracted)
            if items:
                return items

        # ── Try 2: fallback parse on raw text ───────────────────────
        # Handles truncated output where the JSON array isn't properly
        # closed — e.g. the LLM generated many test cases but was
        # cut off before the closing `]`.  We salvage whatever complete
        # objects we can find.
        items = self._fallback_parse(raw)
        if items:
            return items

        import logging

        logger = logging.getLogger(__name__)
        logger.warning(
            "TC generation parsing failed. Raw length=%d, preview=%s",
            len(raw),
            raw[:300],
        )
        return []

    def _fallback_parse(self, text: str) -> list[TestCase]:
        """Fallback: extract individual JSON objects and reconstruct a valid array.

        Handles truncated output, partial objects, and minor syntax errors.
        """
        results: list[TestCase] = []
        for obj_str in self._extract_objects(text):
            try:
                obj = json.loads(obj_str)
                if isinstance(obj, dict) and "id" in obj:
                    results.append(self._dict_to_tc(obj))
            except json.JSONDecodeError:
                fixed = re.sub(r",\s*([\]}])", r"\1", obj_str)
                try:
                    obj = json.loads(fixed)
                    if isinstance(obj, dict) and "id" in obj:
                        results.append(self._dict_to_tc(obj))
                except json.JSONDecodeError:
                    continue
        return results

    @staticmethod
    def _extract_objects(text: str) -> list[str]:
        """Extract top-level JSON objects by matching curly braces."""
        objects: list[str] = []
        i = 0
        while i < len(text):
            start = text.find("{", i)
            if start < 0:
                break
            depth = 0
            j = start
            while j < len(text):
                if text[j] == "{":
                    depth += 1
                elif text[j] == "}":
                    depth -= 1
                    if depth == 0:
                        objects.append(text[start : j + 1])
                        i = j + 1
                        break
                j += 1
            else:
                break
        return objects

    def _extract_json(self, raw: str) -> str:
        """Extract the outermost JSON array from the LLM response.

        Tries, in order:
        1. Markdown-fenced code blocks (```json ... ```)
        2. Bracket-matched extraction (find first [ and its matching ])
        3. Bare text starting with [
        """
        # 1. Markdown-fenced block
        match = re.search(
            r"```(?:json)?\s*\n?(.+?)\n?```", raw, re.DOTALL
        )
        if match:
            candidate = match.group(1).strip()
            extracted = self._extract_brackets(candidate)
            if extracted:
                return extracted

        # 2. Bracket-matched extraction on whole text
        extracted = self._extract_brackets(raw)
        if extracted:
            return extracted

        # 3. Bare text starting with [
        stripped = raw.strip()
        if stripped.startswith("["):
            return stripped

        return ""

    @staticmethod
    def _extract_brackets(text: str) -> str:
        """Find the outermost balanced [...] and return it."""
        start = text.find("[")
        if start < 0:
            return ""
        depth = 0
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        return ""

    _KNOWN_ACTIONS = frozenset({"exec", "launch", "assert", "tap", "type", "swipe", "screenshot", "wait"})

    def _normalize_step(self, s: dict) -> dict:
        """Normalize a raw step dict.

        Handles common LLM quirks:
        - LLM puts command in action field (e.g. action: 'cmd connectivity airplane-mode disable')
        - LLM uses non-standard action names
        """
        s = dict(s)  # copy
        action = (s.get("action") or "").strip()
        target = (s.get("target") or "").strip()

        if action not in self._KNOWN_ACTIONS:
            if target:
                # Unknown action with a target — keep the action but mark it
                s["action"] = action
            elif not target and action:
                # LLM put the command in the action field — treat as exec
                s["action"] = "exec"
                s["target"] = action
            else:
                s["action"] = "exec"

        # Ensure target exists for actions that need it
        if s["action"] in ("tap", "assert", "launch", "type") and not s.get("target", ""):
            # Try to use value as target
            value = (s.get("value") or "").strip()
            if value:
                s["target"] = value

        # Ensure target is populated
        if not s.get("target", ""):
            s["target"] = "unknown"

        return s

    def _dict_to_tc(self, item: dict) -> TestCase:
        """Convert a raw dict to a TestCase."""
        steps_data = item.get("steps", [])
        steps = [TestStep(**self._normalize_step(s)) for s in steps_data] if steps_data else []

        return TestCase(
            id=item.get("id", "TC-UNKNOWN-001"),
            title=item.get("title", ""),
            priority=item.get("priority", "P1"),
            is_core=item.get("is_core", False),
            requirement_ids=item.get("requirement_ids", []),
            steps=steps,
        )
