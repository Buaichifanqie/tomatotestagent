"""Regression script data types.

Mirrors the multi-anchor locator design from the plan:

- ``RegressionScript``: Full script file (tc_id, app_version, steps)
- ``ScriptStep``: Single step with locators and healing info
- ``ScriptLocator``: Multi-anchor locator (resource_id, text, coords, etc.)
- ``HealingRecord``: Structured healing log entry
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class LocatorType(str, Enum):
    """Locator types ordered by stability (highest first)."""
    RESOURCE_ID = "resource_id"
    CONTENT_DESC = "content_desc"
    TEXT = "text"
    CLASS_NAME = "class_name"
    NORMALIZED_COORDS = "normalized_coords"


class ScriptLocator(BaseModel):
    """A single locator for an element, with type and value.

    ``priority`` determines the order of attempt during healing:
    1 = resource_id (most stable), 4 = normalized_coords (least stable).
    """
    type: LocatorType
    value: str
    priority: int = Field(ge=1, le=4)


class ScriptStep(BaseModel):
    """A single step in a regression script.

    Stores the action, target description, and all locator information
    needed for replay and self-healing.  The element_screenshot is a
    relative path (not base64) to keep the JSON compact.
    """
    step: int
    action: str                 # tap / type / swipe / assert / wait / launch / exec
    target: str = ""            # human-readable target description
    value: str = ""             # text to type (for type action)
    expected: str = ""          # expected result (for assert action)
    tap_first: str = ""         # trigger area for hidden controls

    # Multi-anchor locators (ordered by priority)
    locators: list[ScriptLocator] = Field(default_factory=list)

    # Normalized coordinates [x/screen_w, y/screen_h]
    normalized_coords: list[float] = Field(default_factory=list)

    # Element screenshot path (relative to script dir): assets/<tc_id>_step_<n>.png
    element_screenshot: str = ""

    # Fallback target descriptions (from LLM reasoning)
    fallback_targets: list[str] = Field(default_factory=list)

    # Page context for healing
    page_activity: str = ""
    visible_count: int = 0


class ScriptStatus(str, Enum):
    ACTIVE = "active"
    UNSTABLE = "unstable"       # healed 2+ versions in a row, needs review
    EXPIRED = "expired"         # 3+ versions without update
    DEPRECATED = "deprecated"   # explicitly marked for regeneration


class RegressionScript(BaseModel):
    """Complete regression test script.

    Bound to a specific app version with compatibility tracking.
    """
    script_version: str = "1.0"
    tc_id: str = ""
    tc_title: str = ""
    app_name: str = ""                   # stable identifier (e.g. "bilibili")
    app_package: str = ""
    platform: str = "android"            # android / ios
    app_version: str = ""               # version when generated
    compatible_versions: list[str] = Field(default_factory=list)
    min_compatible_version: str = ""
    status: ScriptStatus = ScriptStatus.ACTIVE
    generated_at: str = ""
    last_healed_at: str = ""
    heal_count: int = 0
    run_count: int = 0
    steps: list[ScriptStep] = Field(default_factory=list)

    def to_file_content(self) -> str:
        """Serialize to compact JSON for file storage."""
        return json.dumps(
            self.model_dump(),
            ensure_ascii=False,
            indent=2,
        )

    @classmethod
    def from_file_content(cls, content: str) -> RegressionScript:
        """Deserialize from JSON file content."""
        data = json.loads(content)
        return cls(**data)

    def is_compatible_with(self, app_version: str) -> bool:
        """Check if this script is compatible with the given app version."""
        if not self.compatible_versions:
            return False
        if app_version in self.compatible_versions:
            return True
        # Check min_compatible_version
        if self.min_compatible_version:
            try:
                from packaging.version import Version
                return Version(app_version) >= Version(self.min_compatible_version)
            except Exception:
                pass
        return False

    def mark_run(self) -> None:
        """Increment run counter."""
        self.run_count += 1


class HealingType(str, Enum):
    LOCATOR_RERESOLVE = "locator_reresolve"       # DOM re-match
    TARGET_RENAME = "target_rename"               # text/content-desc changed
    COORDS_SHIFT = "coords_shift"                 # position changed
    VISION_MATCH = "vision_match"                 # Vision-based re-find
    FALLBACK_LLM = "fallback_llm"                 # Fallback to LLM


class HealingRecord(BaseModel):
    """A single self-healing event, appended to healing_log.jsonl."""
    timestamp: str = ""
    tc_id: str = ""
    tc_title: str = ""
    step: int = 0
    heal_type: HealingType = HealingType.LOCATOR_RERESOLVE
    old_target: str = ""
    new_target: str = ""
    old_locator: ScriptLocator | None = None
    new_locator: ScriptLocator | None = None
    confidence: float = 0.0
    method: str = "dom_scan"            # dom_scan / vision / coords
    duration_ms: int = 0
    app_version: str = ""

    def to_jsonl_line(self) -> str:
        """Serialize to a single JSON line for append-only log."""
        data = self.model_dump()
        return json.dumps(data, ensure_ascii=False)

    @classmethod
    def from_jsonl_line(cls, line: str) -> HealingRecord:
        data = json.loads(line)
        return cls(**data)
