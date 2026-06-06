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
    """A single UI action needed to reach an exploration target.

    Accepts both legacy field names (``type`` / ``target_hint`` / ``input_value``)
    and new schema names (``action`` / ``target_hint`` / ``input_value`` or
    ``description`` / ``value``). The legacy field names remain primary for
    backward compatibility with existing callers and tests.
    """

    type: str
    target_hint: str
    input_value: str = ""
    success_signal: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> ReachAction:
        """Create a ReachAction from a dict (e.g. parsed JSON)."""
        action_type = d.get("type") or d.get("action") or ""
        target_hint = d.get("target_hint") or d.get("description") or ""
        input_value = d.get("input_value") or d.get("value") or ""
        success_signal = d.get("success_signal", "")
        return cls(
            type=action_type,
            target_hint=target_hint,
            input_value=input_value,
            success_signal=success_signal,
        )


@dataclass
class ExplorationTarget:
    """A capability identified by the planner as worth exploring.

    Backward compatible: ``target_name`` / ``keywords`` / ``reach_actions``
    stay as primary fields. New ``capability_*`` fields are populated when
    present in the LLM output.
    """

    target_name: str
    keywords: list[str] = field(default_factory=list)
    reach_actions: list[ReachAction] = field(default_factory=list)
    priority: int = 2
    # New capability-oriented fields (optional)
    capability_id: str = ""
    capability_name: str = ""
    source_evidence: str = ""
    inferred: bool = False
    preconditions: list[str] = field(default_factory=list)
    entry_points: list[dict] = field(default_factory=list)
    reach_plan: list[ReachAction] = field(default_factory=list)
    screens_to_capture: list[str] = field(default_factory=list)
    branch_points: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> ExplorationTarget:
        """Create an ExplorationTarget from a dict (e.g. parsed JSON).

        Accepts both legacy and new schemas. When the new schema is present,
        legacy fields are auto-derived if missing.
        """
        capability_name = d.get("capability_name", "")
        legacy_name = d.get("target_name") or capability_name
        if not legacy_name:
            raise KeyError("target_name or capability_name is required")

        legacy_keywords = d.get("keywords")
        if legacy_keywords is None:
            # Derive from first entry_point if available
            entry_points = d.get("entry_points") or []
            if entry_points and isinstance(entry_points[0], dict):
                legacy_keywords = entry_points[0].get("keywords", [])
            else:
                legacy_keywords = []

        # reach_actions: prefer explicit legacy field, else derive from reach_plan
        raw_reach_actions = d.get("reach_actions")
        if raw_reach_actions is None:
            raw_reach_plan = d.get("reach_plan", [])
            # Filter to tap/type only for legacy compatibility
            raw_reach_actions = [
                s for s in raw_reach_plan
                if isinstance(s, dict) and (s.get("type") or s.get("action")) in {"tap", "type"}
            ]

        reach_plan_raw = d.get("reach_plan", [])

        return cls(
            target_name=legacy_name,
            keywords=legacy_keywords,
            reach_actions=[ReachAction.from_dict(a) for a in raw_reach_actions],
            priority=d.get("priority", 2),
            capability_id=d.get("capability_id", ""),
            capability_name=capability_name,
            source_evidence=d.get("source_evidence", ""),
            inferred=bool(d.get("inferred", False)),
            preconditions=d.get("preconditions", []),
            entry_points=d.get("entry_points", []),
            reach_plan=[ReachAction.from_dict(a) for a in reach_plan_raw],
            screens_to_capture=d.get("screens_to_capture", []),
            branch_points=d.get("branch_points", []),
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
