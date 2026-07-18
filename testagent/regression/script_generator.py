"""Script generator — converts executed TestCase steps into a RegressionScript.

Called after a regression-marked TC executes successfully (all steps pass).
Extracts locators, normalized coordinates, and element screenshots from the
StepExecution results.
"""
from __future__ import annotations

import base64
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from testagent.plan.models import TestCase, StepExecution
from testagent.regression.types import (
    RegressionScript,
    ScriptLocator,
    ScriptStep,
    ScriptStatus,
    LocatorType,
)


class ScriptGenerator:
    """Generates a RegressionScript from a successfully executed TestCase."""

    def __init__(self, output_dir: str = "") -> None:
        self._output_dir = Path(output_dir) if output_dir else Path.cwd()
        self._assets_dir = self._output_dir / "assets"
        self._assets_dir.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        tc: TestCase,
        app_package: str,
        app_name: str = "",
        app_version: str = "",
        platform: str = "android",
        screen_width: int = 1080,
        screen_height: int = 2400,
    ) -> RegressionScript:
        """Generate a regression script from a completed TestCase."""
        script_steps: list[ScriptStep] = []
        now = datetime.now(timezone.utc).isoformat()

        for i, step_exec in enumerate(tc.execution.steps):
            step = tc.steps[i] if i < len(tc.steps) else None
            script_step = self._build_script_step(
                step_exec=step_exec,
                step_idx=i + 1,
                original_step=step,
                screen_w=screen_width,
                screen_h=screen_height,
                assets_dir=self._assets_dir,
                tc_id=tc.id,
            )
            script_steps.append(script_step)

        return RegressionScript(
            script_version="1.0",
            tc_id=tc.id,
            tc_title=tc.title,
            app_name=app_name,
            app_package=app_package,
            platform=platform,
            app_version=app_version,
            compatible_versions=[app_version],
            min_compatible_version=app_version,
            status=ScriptStatus.ACTIVE,
            generated_at=now,
            steps=script_steps,
        )

    def _build_script_step(
        self,
        step_exec: StepExecution,
        step_idx: int,
        original_step: Any,
        screen_w: int,
        screen_h: int,
        assets_dir: Path,
        tc_id: str,
    ) -> ScriptStep:
        """Build a single ScriptStep from execution results."""
        locators: list[ScriptLocator] = []
        fallback_targets: list[str] = []
        normalized_coords: list[float] = []
        element_screenshot_path = ""
        page_activity = ""
        visible_count = 0

        coords = step_exec.coords or {}
        if coords.get("x") is not None and screen_w > 0:
            nx = max(0.0, min(1.0, coords["x"] / screen_w))
            ny = max(0.0, min(1.0, coords["y"] / screen_h))
            normalized_coords = [round(nx, 4), round(ny, 4)]

        source_data = self._parse_source_metadata(step_exec.source)
        for loc in self._build_locators_from_source(source_data):
            if loc not in locators:
                locators.append(loc)

        if normalized_coords and len(normalized_coords) == 2:
            locators.append(ScriptLocator(
                type=LocatorType.NORMALIZED_COORDS,
                value=f"{normalized_coords[0]},{normalized_coords[1]}",
                priority=4,
            ))

        if original_step and original_step.target:
            fallback_targets = self._generate_fallback_targets(original_step.target)

        scr_after = step_exec.screenshot_after
        if scr_after:
            try:
                src_path = Path(scr_after)
                if src_path.exists():
                    asset_name = f"{tc_id}_step_{step_idx}.png"
                    asset_path = assets_dir / asset_name
                    asset_path.write_bytes(src_path.read_bytes())
                    element_screenshot_path = f"assets/{asset_name}"
            except Exception:
                pass

        page_activity = ""
        visible_count = step_exec.matched_count or 0

        target = original_step.target if original_step else ""
        value = original_step.value if original_step else ""
        expected = original_step.expected if original_step else ""
        tap_first = original_step.tap_first if original_step else ""

        return ScriptStep(
            step=step_idx,
            action=step_exec.action,
            target=target,
            value=value,
            expected=expected,
            tap_first=tap_first,
            locators=locators,
            normalized_coords=normalized_coords,
            element_screenshot=element_screenshot_path,
            fallback_targets=fallback_targets,
            page_activity=page_activity,
            visible_count=visible_count,
        )

    @staticmethod
    def _build_locators_from_source(source_data: dict) -> list[ScriptLocator]:
        locators: list[ScriptLocator] = []
        if source_data.get("resource_id"):
            locators.append(ScriptLocator(type=LocatorType.RESOURCE_ID, value=source_data["resource_id"], priority=1))
        if source_data.get("content_desc"):
            locators.append(ScriptLocator(type=LocatorType.CONTENT_DESC, value=source_data["content_desc"], priority=2))
        if source_data.get("text"):
            locators.append(ScriptLocator(type=LocatorType.TEXT, value=source_data["text"], priority=3))
        return locators

    @staticmethod
    def _parse_source_metadata(source: str) -> dict[str, str]:
        return {}

    @staticmethod
    def _generate_fallback_targets(target: str) -> list[str]:
        fallbacks: list[str] = [target]
        for prefix in ["顶部", "底部", "页面", "左侧", "右侧"]:
            if target.startswith(prefix):
                fallbacks.append(target[len(prefix):])
        if "框" in target:
            fallbacks.append("输入框")
            fallbacks.append("搜索框")
        if "按钮" in target:
            fallbacks.append(target.replace("按钮", "").strip())
        if "Tab" in target or "tab" in target:
            fallbacks.append("导航栏")
        return list(dict.fromkeys(fallbacks))
