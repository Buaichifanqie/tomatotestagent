from __future__ import annotations

import json
import re
from typing import Any

from testagent.plan.models import TestCase, TestStep

# ── 四类场景覆盖的测试用例生成提示词 ─────────────────────────────
TC_GENERATION_SYSTEM_PROMPT = (
    "You are a senior QA engineer generating test cases for an Android mobile app.\n"
    "\n"
    "## Four scenario types to cover\n"
    "\n"
    "For each function point, generate test cases across these categories:\n"
    "\n"
    "1. **Positive (normal flow)** – The core business flow, the most common user path,\n"
    "   end-to-end operation chains.\n"
    "\n"
    "2. **Negative (abnormal operation)** – Input validation, null/empty handling,\n"
    "   duplicate operations, interrupted flows.\n"
    "\n"
    "3. **Boundary (boundary conditions)** – Equivalence class boundaries, max/min input,\n"
    "   special characters, format boundary checks.\n"
    "\n"
    "4. **Permission / Auth** – Unauthenticated access, unauthorized operations,\n"
    "   cross-role access.\n"
    "\n"
    "## Priority levels\n"
    "\n"
    "- **P0** – Core functionality, blocking issues (10-15% of total)\n"
    "- **P1** – Important functionality, non-blocking but impactful (30-40%)\n"
    "- **P2** – Edge cases, UX polish\n"
    "- **P3** – Rare edge cases\n"
    "\n"
    "## Naming convention\n"
    "\n"
    'Use `TC-{MODULE}-{NUM}` for IDs, e.g. `TC-SEARCH-001`, `TC-LOGIN-002`.\n'
    "Case names must clearly express the test intent.\n"
    "\n"
    "## Allowed step actions\n"
    "\n"
    "The `action` field in each step MUST be one of the following exact values:\n"
    "\n"
    '- `"tap"` – Tap/click a UI element. Use for all taps, clicks, presses.\n'
    '- `"type"` – Type text into an input field. Set `value` to the text to input.\n'
    '- `"launch"` – Launch an Android app by package name. Set `target` to the package.\n'
    '  **IMPORTANT:** Always use `"launch"` to open the app. NEVER use `"exec"` with\n'
    '  `am start` or `monkey` for launching. `"exec"` is only for device-level\n'
    '  operations like `svc wifi disable`, `pm clear`, `input keyevent`, etc.\n'
    '- `"swipe"` – Swipe gesture. Set `target` to "start_x,start_y,end_x,end_y".\n'
    '- `"assert"` – Assert/verify a UI element is visible.\n'
    '  **CRITICAL:** `target` must be a SHORT text label actually visible on screen,\n'
    '  e.g. `"推荐"`, `"热门"`, `"关注"`, `"我的"`. NEVER use long descriptive phrases\n'
    '  like `"显示首页推荐内容"` or `"首页正常加载"` — those are not real UI elements.\n'
    '- `"exec"` – Execute an Android shell command (adb).\n'
    '- `"screenshot"` – Take a screenshot for visual verification.\n'
    "\n"
    "## Platform constraints (Android mobile, NOT web)\n"
    "\n"
    "- This is an **Android mobile app**, not a web browser.\n"
    "- Do NOT use web concepts like 'navigate to URL', 'cookie', 'viewport',\n"
    '  "browser", "address bar", or "HTTP".\n'
    "- All actions must be mobile UI interactions: tap buttons, type in text fields,\n"
    "  swipe to scroll, long-press, etc.\n"
    "- Use `launch` (with app package name) to open the app.\n"
    "- Use `exec` for device-level operations like toggling wifi.\n"
    "- Use `tap` on visible UI elements described by their Android UI labels.\n"
    "\n"
    "## Output format\n"
    "\n"
    "Output ONLY a valid JSON array. No markdown, no explanation, no code fences.\n"
    "Return raw JSON that starts with `[` and ends with `]`.\n"
    "\n"
    "Each test case object:\n"
    "```json\n"
    "{\n"
    '  "id": "TC-MODULE-001",\n'
    '  "title": "concise test case name",\n'
    '  "priority": "P0",\n'
    '  "is_core": true,\n'
    '  "requirement_ids": ["REQ-001"],\n'
    "  \"steps\": [\n"
    '    {"step": 1, "action": "launch", "target": "tv.danmaku.bili", "value": ""},\n'
    '    {"step": 2, "action": "assert", "target": "推荐", "value": ""},\n'
    '    {"step": 3, "action": "screenshot", "target": "", "value": ""}\n'
    "  ]\n"
    "}\n"
    "```\n"
    "\n"
    "Coverage requirements:\n"
    "- Each function point must have at least 1 positive + 1 negative scenario\n"
    "- P0 cases must include complete step-by-step actions\n"
    "- Do NOT invent requirements not in the input\n"
    "- No duplicate test cases\n"
)


class TestCaseGenerator:
    """Generates structured test cases from PRD text using an LLM or fallback."""

    def __init__(self, llm_provider: Any = None) -> None:
        self._llm_provider = llm_provider

    # ── public API ───────────────────────────────────────────────────────────

    def generate(
        self, prd_text: str, plan_name: str = ""
    ) -> list[TestCase]:
        if self._llm_provider is not None:
            raw = self._call_llm(prd_text)
        else:
            raw = prd_text
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
        if not extracted:
            return []

        # Pre-clean: remove trailing commas before ] or } (common LLM issue)
        cleaned = re.sub(r",\s*([\]}])", r"\1", extracted)

        # Try strict JSON parse first
        try:
            items = json.loads(cleaned)
            if isinstance(items, list):
                return [self._dict_to_tc(item) for item in items if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass

        # Fallback: extract individual objects via regex and rebuild array
        items = self._fallback_parse(extracted)
        if items:
            return items

        import logging

        logger = logging.getLogger(__name__)
        logger.warning(
            "TC generation parsing failed. Extracted length=%d, preview=%s",
            len(extracted),
            extracted[:500],
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
