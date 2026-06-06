from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from testagent.plan.models import TestCase, TestStep

# ── Prompt loading ───────────────────────────────────────────────
_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(filename: str) -> str:
    """Load a prompt template from the prompts directory."""
    return (_PROMPTS_DIR / filename).read_text(encoding="utf-8")


TC_GENERATION_SYSTEM_PROMPT = _load_prompt("tc_generation.txt")


class TestCaseGenerator:
    """Generates structured test cases from PRD text using an LLM or fallback."""

    def __init__(self, llm_provider: Any = None) -> None:
        self._llm_provider = llm_provider
        self.last_raw_output: str = ""  # populated after each generate() call

    # ── public API ───────────────────────────────────────────────────────────

    async def generate(
        self, prd_text: str, plan_name: str = ""
    ) -> list[TestCase]:
        if self._llm_provider is not None:
            raw = await self._call_llm(prd_text)
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

    async def _call_llm(self, prd_text: str) -> str:
        """Call the LLM provider and return the raw response text."""
        result = self._llm_provider(prd_text)  # type: ignore[misc]
        if hasattr(result, "__await__"):
            result = await result
        return result

    # ── response parsing ─────────────────────────────────────────────────────

    def _parse_response(self, raw: str) -> list[TestCase]:
        """Parse the LLM response into a list of TestCase objects.

        Supports two top-level shapes from the LLM:
          1. ``{"_meta": {...}, "cases": [ {...}, ... ]}``  (new schema)
          2. ``[ {...}, ... ]``                              (legacy schema)
        """
        # ── Try 0: top-level object with a "cases" array (new schema) ──
        items_from_object = self._extract_cases_from_object(raw)
        if items_from_object is not None:
            return [self._dict_to_tc(item) for item in items_from_object if isinstance(item, dict)]

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

    def _extract_cases_from_object(self, raw: str) -> list[dict] | None:
        """If the LLM returned ``{"_meta":..., "cases":[...]}``, return the cases list.

        Returns None when the response is not a top-level object with ``cases``,
        so callers can fall back to legacy array parsing.
        """
        text = raw.strip()
        # Strip markdown fences if present
        fence_match = re.search(r"```(?:json)?\s*\n?(.+?)\n?```", text, re.DOTALL)
        if fence_match:
            text = fence_match.group(1).strip()

        # Find the outermost {...}
        start = text.find("{")
        if start < 0:
            return None
        depth = 0
        end = -1
        for i in range(start, len(text)):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end < 0:
            return None

        candidate = text[start:end + 1]
        candidate = re.sub(r",\s*([\]}])", r"\1", candidate)
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            return None
        if not isinstance(obj, dict):
            return None
        cases = obj.get("cases")
        if not isinstance(cases, list):
            return None
        return cases

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
    _KNOWN_STEP_FIELDS = frozenset({
        "step", "action", "target", "value", "expected",
        "timeout_ms", "poll_interval_ms", "wait_after",
        "success_condition", "screenshot", "is_manual", "instruction",
    })

    def _normalize_step(self, s: dict) -> dict:
        """Normalize a raw step dict.

        Handles common LLM quirks:
        - LLM puts command in action field (e.g. action: 'cmd connectivity airplane-mode disable')
        - LLM uses non-standard action names
        - LLM includes unknown fields (filtered out to prevent Pydantic errors)
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

        # Filter unknown fields to prevent Pydantic validation errors
        return {k: v for k, v in s.items() if k in self._KNOWN_STEP_FIELDS}

    def _dict_to_tc(self, item: dict) -> TestCase:
        """Convert a raw dict to a TestCase."""
        steps_data = item.get("steps", [])
        steps = [TestStep(**self._normalize_step(s)) for s in steps_data] if steps_data else []

        # The v2 prompt uses a unified ``preconditions`` array with ``state:*``
        # prefixes for mechanism-level states (e.g. ``state:logged_in``) and
        # ``biz:*`` / ``data:*`` for business-level prerequisites.
        # We split them into the legacy ``required_state`` (strip prefix) and
        # the new ``prerequisites`` (keep as-is).
        raw_preconditions = item.get("preconditions", [])
        required_state: list[str] = []
        prerequisites: list[str] = []
        if isinstance(raw_preconditions, list):
            for p in raw_preconditions:
                p_str = str(p)
                if p_str.startswith("state:"):
                    required_state.append(p_str.removeprefix("state:"))
                else:
                    prerequisites.append(p_str)

        # If the caller provided the old ``required_state`` directly, use it
        # (fall back to what we just derived from preconditions).
        legacy_required_state = item.get("required_state", [])
        if not legacy_required_state and required_state:
            legacy_required_state = required_state

        return TestCase(
            id=item.get("id", "TC-UNKNOWN-001"),
            title=item.get("title", ""),
            priority=item.get("priority", "P1"),
            is_core=item.get("is_core", False),
            feature_id=item.get("feature_id", ""),
            coverage_dimension=item.get("coverage_dimension", ""),
            scenario_question=item.get("scenario_question", ""),
            prerequisites=prerequisites,
            expected_outcome=item.get("expected_outcome", ""),
            requirement_ids=item.get("requirement_ids", []),
            required_state=legacy_required_state,
            steps=steps,
        )
