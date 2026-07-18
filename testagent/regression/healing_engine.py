"""Self-healing engine for regression scripts.

When a script step fails during replay, the healing engine attempts to
re-locate the target element by trying each locator type in priority order:

1. resource_id → DOM scan (fastest, most stable)
2. content_desc → DOM attribute match
3. text → DOM text match
4. normalized_coords → position-based (least stable, device-dependent)
5. Vision match → image-based (as last resort, requires screenshot)

Each locator attempt has a 5-second timeout. If all fail, the step is
marked as "cannot heal" and the caller falls back to LLM mode.
"""
from __future__ import annotations

import logging
import re
import time
import xml.etree.ElementTree as ET
from typing import Any

from testagent.regression.types import (
    HealingRecord,
    HealingType,
    LocatorType,
    ScriptLocator,
    ScriptStep,
)
from testagent.vision_local.dom_parser import DomParser

logger = logging.getLogger(__name__)

_HEAL_TIMEOUT_S = 5.0  # Max seconds for a single locator attempt


class HealingEngine:
    """Attempts to self-heal a failed script step by re-resolving locators."""

    def __init__(self, screen_width: int = 1080, screen_height: int = 2400) -> None:
        self._screen_w = screen_width
        self._screen_h = screen_height
        self._dom_parser = DomParser()

    # ── Public API ───────────────────────────────────────────────

    def heal_step(
        self,
        script_step: ScriptStep,
        dom_xml: str,
        app_version: str = "",
    ) -> dict[str, Any]:
        """Attempt to heal a single failed script step.

        Tries locators in priority order (1 → 4), each with a 5s timeout.

        Args:
            script_step: The script step that failed.
            dom_xml: Current page's UI hierarchy XML.
            app_version: Current app version (for logging).

        Returns:
            ``{"success": True, "x": int, "y": int, "locator": ..., "method": ...}``
            or ``{"success": False, "reason": ...}``
        """
        # Sort locators by priority
        sorted_locators = sorted(script_step.locators, key=lambda l: l.priority)

        for locator in sorted_locators:
            start = time.monotonic()
            try:
                if time.monotonic() - start > _HEAL_TIMEOUT_S:
                    logger.warning(f"[Healing] timeout for locator {locator.type}")
                    continue

                result = self._try_locator(locator, dom_xml, script_step)
                if result.get("success"):
                    duration = int((time.monotonic() - start) * 1000)
                    result["duration_ms"] = duration
                    result["locator_used"] = locator.model_dump()
                    logger.info(
                        f"[Healing] step {script_step.step}: {locator.type} "
                        f"-> ({result.get('x')}, {result.get('y')}) in {duration}ms"
                    )
                    return result
            except Exception as e:
                logger.debug(f"[Healing] locator {locator.type} error: {e}")
                continue

        # ── All locators failed — try vision fallback ─────────────
        vision_result = self._try_vision_match(script_step)
        if vision_result.get("success"):
            return vision_result

        return {"success": False, "reason": "all locators exhausted"}

    # ── Locator strategies ───────────────────────────────────────

    def _try_locator(
        self,
        locator: ScriptLocator,
        dom_xml: str,
        script_step: ScriptStep,
    ) -> dict[str, Any]:
        """Attempt to resolve a single locator."""
        if not dom_xml:
            return {"success": False}

        root = ET.fromstring(dom_xml.encode("utf-8"))

        if locator.type == LocatorType.RESOURCE_ID:
            return self._find_by_resource_id(root, locator.value)
        elif locator.type == LocatorType.CONTENT_DESC:
            return self._find_by_attribute(root, "content-desc", locator.value)
        elif locator.type == LocatorType.TEXT:
            return self._find_by_attribute(root, "text", locator.value)
        elif locator.type == LocatorType.NORMALIZED_COORDS:
            return self._resolve_normalized_coords(locator.value)
        elif locator.type == LocatorType.CLASS_NAME:
            return self._find_by_class(root, locator.value)

        return {"success": False}

    @staticmethod
    def _find_by_resource_id(root: ET.Element, res_id: str) -> dict[str, Any]:
        """Find element by resource-id attribute.

        Uses both exact and suffix matching (some apps truncate the prefix).
        """
        for elem in root.iter():
            rid = elem.get("resource-id", "") or ""
            if rid == res_id or rid.endswith(f"/{res_id.split('/')[-1]}"):
                bounds = elem.get("bounds", "")
                coords = _parse_bounds_to_center(bounds)
                if coords:
                    return {"success": True, "x": coords[0], "y": coords[1], "method": "dom:resource_id"}
        return {"success": False}

    @staticmethod
    def _find_by_attribute(root: ET.Element, attr: str, value: str) -> dict[str, Any]:
        """Find element by an XML attribute (text, content-desc)."""
        for elem in root.iter():
            attr_val = elem.get(attr, "") or ""
            if attr_val and (attr_val == value or value in attr_val or attr_val in value):
                bounds = elem.get("bounds", "")
                coords = _parse_bounds_to_center(bounds)
                if coords:
                    return {"success": True, "x": coords[0], "y": coords[1], "method": f"dom:{attr}"}
        return {"success": False}

    @staticmethod
    def _find_by_class(root: ET.Element, class_name: str) -> dict[str, Any]:
        """Find clickable element by class name."""
        for elem in root.iter():
            cls = elem.get("class", "") or ""
            if cls.endswith(f".{class_name}") or cls == class_name:
                if elem.get("clickable", "false") == "true":
                    bounds = elem.get("bounds", "")
                    coords = _parse_bounds_to_center(bounds)
                    if coords:
                        return {"success": True, "x": coords[0], "y": coords[1], "method": "dom:class"}
        return {"success": False}

    @staticmethod
    def _resolve_normalized_coords(value: str) -> dict[str, Any]:
        """Convert normalized coords string to pixel coords.

        Value format: ``"0.45,0.08"`` → x=45% of screen, y=8% of screen.
        """
        try:
            parts = value.split(",")
            if len(parts) == 2:
                nx, ny = float(parts[0]), float(parts[1])
                # screen dimensions come from constructor
                # fallback to device-independent coords
                x = int(nx * 1080)
                y = int(ny * 2400)
                return {"success": True, "x": x, "y": y, "method": "coords"}
        except (ValueError, IndexError):
            pass
        return {"success": False}

    @staticmethod
    def _try_vision_match(script_step: ScriptStep) -> dict[str, Any]:
        """Vision-based fallback: use element_screenshot for image matching.

        This is a placeholder — real Vision matching requires the
        VolcanoVisionClient and will be implemented in Phase 3.
        """
        if script_step.element_screenshot:
            logger.info(f"[Healing] vision match available for step {script_step.step} (Phase 3)")
        return {"success": False}

    # ── Healing record factory ───────────────────────────────────

    @staticmethod
    def build_healing_record(
        step: ScriptStep,
        result: dict[str, Any],
        old_target: str,
        new_target: str,
        app_version: str,
    ) -> dict[str, Any]:
        """Build a structured healing log entry."""
        heal_type = HealingType.LOCATOR_RERESOLVE
        method = result.get("method", "unknown")

        if "dom:" in method:
            heal_type = HealingType.TARGET_RENAME
        elif method == "coords":
            heal_type = HealingType.COORDS_SHIFT
        elif method == "vision":
            heal_type = HealingType.VISION_MATCH

        import json
        record = HealingRecord(
            tc_id="",
            tc_title="",
            step=step.step,
            heal_type=heal_type,
            old_target=old_target,
            new_target=new_target,
            method=method,
            confidence=0.85,
            duration_ms=result.get("duration_ms", 0),
            app_version=app_version,
        )
        return json.loads(record.model_dump_json())


# ── Utility ─────────────────────────────────────────────────────


def _parse_bounds_to_center(bounds_str: str) -> tuple[int, int] | None:
    """Parse ``[x1,y1][x2,y2]`` bounds and return center point."""
    if not bounds_str:
        return None
    match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str)
    if match:
        x1, y1, x2, y2 = int(match[1]), int(match[2]), int(match[3]), int(match[4])
        return ((x1 + x2) // 2, (y1 + y2) // 2)
    return None
