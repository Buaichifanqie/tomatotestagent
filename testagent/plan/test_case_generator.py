from __future__ import annotations

import json
import re
from typing import Any

from testagent.plan.models import TestCase, TestStep

TC_GENERATION_SYSTEM_PROMPT = (
    "You are a QA engineer. Generate a JSON array of test cases from the "
    "following PRD. Each test case must have: id (str), title (str), "
    "priority (str, one of P0/P1/P2/P3), is_core (bool), "
    "requirement_ids (list[str]), and steps (list of objects with step (int), "
    "action (str), target (str), value (str)). "
    "Output ONLY a valid JSON array, no additional text."
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
        return self._parse_response(raw)

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
        try:
            items = json.loads(extracted)
        except json.JSONDecodeError:
            return []
        if not isinstance(items, list):
            return []
        return [self._dict_to_tc(item) for item in items]

    def _extract_json(self, raw: str) -> str:
        """Extract a JSON array from markdown-fenced or bare text."""
        # Try markdown-fenced block first
        match = re.search(
            r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL
        )
        if match:
            candidate = match.group(1).strip()
        else:
            candidate = raw.strip()

        # Quick validity check -- must start with '['
        if not candidate.startswith("["):
            return ""
        return candidate

    def _dict_to_tc(self, item: dict) -> TestCase:
        """Convert a raw dict to a TestCase."""
        steps_data = item.get("steps", [])
        steps = [TestStep(**s) for s in steps_data] if steps_data else []

        return TestCase(
            id=item["id"],
            title=item["title"],
            priority=item.get("priority", "P1"),
            is_core=item.get("is_core", False),
            requirement_ids=item.get("requirement_ids", []),
            steps=steps,
        )
