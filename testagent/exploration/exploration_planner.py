"""Exploration planner: uses an LLM to extract exploration targets from PRD text.

Called by AppExplorer (Task 5) to get a list of pages that need to be explored
before generating test cases.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

__all__ = ["ReachAction", "ExplorationTarget", "ExplorationPlanner"]

_log = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent.parent / "plan" / "prompts" / "exploration_planner.txt"


@dataclass
class ReachAction:
    """A single UI action needed to reach an exploration target."""

    type: str
    target_hint: str
    input_value: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> ReachAction:
        """Create a ReachAction from a dict (e.g. parsed JSON)."""
        return cls(
            type=d["type"],
            target_hint=d["target_hint"],
            input_value=d.get("input_value", ""),
        )


@dataclass
class ExplorationTarget:
    """A page / screen identified by the planner as worth exploring."""

    target_name: str
    keywords: list[str] = field(default_factory=list)
    reach_actions: list[ReachAction] = field(default_factory=list)
    priority: int = 2

    @classmethod
    def from_dict(cls, d: dict) -> ExplorationTarget:
        """Create an ExplorationTarget from a dict (e.g. parsed JSON)."""
        return cls(
            target_name=d["target_name"],
            keywords=d.get("keywords", []),
            reach_actions=[ReachAction.from_dict(a) for a in d.get("reach_actions", [])],
            priority=d.get("priority", 2),
        )


class ExplorationPlanner:
    """Plans exploration targets by asking an LLM to analyse a PRD document.

    Parameters
    ----------
    llm_callable : async (str) -> str
        An async function that sends a prompt to an LLM and returns its text
        response.  Typically produced by ``_build_llm_callable()`` in plan.py.
    """

    def __init__(self, llm_callable: Callable[[str], Awaitable[str]]) -> None:
        self._llm = llm_callable

    async def plan(self, prd_text: str) -> list[ExplorationTarget]:
        """Analyse *prd_text* and return exploration targets sorted by priority.

        Returns an empty list on any failure (LLM error, invalid JSON, etc.).
        """
        prompt = self._load_prompt()
        full_prompt = f"{prompt}\n\n---\n\n{prd_text}"

        try:
            raw = await self._llm(full_prompt)
        except Exception:
            _log.warning("LLM call failed during exploration planning", exc_info=True)
            return []

        return self._parse_response(raw)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _load_prompt() -> str:
        """Load the system prompt template from the prompts directory."""
        try:
            return _PROMPT_PATH.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            _log.warning("Exploration planner prompt not found at %s", _PROMPT_PATH)
            return ""

    @staticmethod
    def _parse_response(raw: str) -> list[ExplorationTarget]:
        """Parse LLM response into ExplorationTarget objects.

        Handles raw JSON as well as JSON wrapped in markdown fences.
        Returns an empty list if parsing fails.
        """
        text = raw.strip()

        # Strip markdown code fences if present
        if text.startswith("```"):
            # Remove first line (```json or ```)
            first_nl = text.find("\n")
            if first_nl != -1:
                text = text[first_nl + 1 :]
            # Remove trailing ```
            if text.rstrip().endswith("```"):
                text = text.rstrip()[: -len("```")].rstrip()

        # Try parsing as-is first
        data = None
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass

        # If that failed, try to extract a JSON array from the text
        if data is None:
            # Find the first [ and last ]
            start = text.find("[")
            end = text.rfind("]")
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end + 1])
                except (json.JSONDecodeError, ValueError):
                    # Try fixing common LLM JSON issues
                    candidate = text[start:end + 1]
                    # Remove trailing commas before ] or }
                    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
                    try:
                        data = json.loads(candidate)
                    except (json.JSONDecodeError, ValueError):
                        _log.warning(
                            "Failed to parse LLM response as JSON",
                            exc_info=True,
                        )
                        return []

        if data is None:
            _log.warning("Failed to parse LLM response as JSON")
            return []

        if not isinstance(data, list):
            _log.warning("LLM response JSON is not a list: %s", type(data).__name__)
            return []

        targets: list[ExplorationTarget] = []
        for item in data:
            try:
                targets.append(ExplorationTarget.from_dict(item))
            except (KeyError, TypeError, ValueError):
                _log.warning("Skipping invalid target entry: %s", item, exc_info=True)
                continue

        targets.sort(key=lambda t: t.priority)
        return targets
