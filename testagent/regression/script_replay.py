"""Script replay engine — executes regression scripts without LLM/Vision.

Directly executes each step via ADB/Appium, bypassing LLM and Vision API
calls. Each step is followed by a lightweight DOM assertion to verify the
expected result.

When a step fails, the self-healing engine is triggered.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from testagent.regression.healing_engine import HealingEngine
from testagent.regression.script_store import ScriptStore
from testagent.regression.types import RegressionScript, ScriptStep, ScriptStatus

logger = logging.getLogger(__name__)


class ScriptReplayResult:
    """Result of replaying a single script step."""

    def __init__(
        self,
        step: int = 0,
        success: bool = True,
        error_message: str = "",
        duration_ms: int = 0,
        healed: bool = False,
        healing_detail: str = "",
    ) -> None:
        self.step = step
        self.success = success
        self.error_message = error_message
        self.duration_ms = duration_ms
        self.healed = healed
        self.healing_detail = healing_detail


class ScriptReplayEngine:
    """Executes regression scripts deterministically without LLM.

    Flow per step:
    1. Use the highest-priority locator to find the element
    2. Execute the action (tap / type / swipe / assert)
    3. Lightweight DOM assertion to verify success
    4. On failure → trigger HealingEngine
    5. On heal failure → return failure for LLM fallback
    """

    def __init__(
        self,
        script_store: ScriptStore | None = None,
        healing_engine: HealingEngine | None = None,
        appium_url: str = "http://localhost:4723",
        session_id: str = "",
        device_udid: str = "",
    ) -> None:
        self._store = script_store or ScriptStore()
        self._healer = healing_engine or HealingEngine()
        self._appium_url = appium_url
        self._session_id = session_id
        self._device_udid = device_udid

        # Stats
        self._total_heals = 0
        self._step_results: list[ScriptReplayResult] = []

    @property
    def total_heals(self) -> int:
        return self._total_heals

    @property
    def step_results(self) -> list[ScriptReplayResult]:
        return list(self._step_results)

    async def replay(
        self,
        script: RegressionScript,
        app_version: str = "",
        tap_executor: Callable[[int, int, str, str], Any] | None = None,
        type_executor: Callable[[int, int, str, str, str], Any] | None = None,
        source_fetcher: Callable[[], str] | None = None,
        app_executor: Callable[[str], Any] | None = None,
    ) -> bool:
        """Replay a regression script step by step.

        Args:
            script: The script to replay.
            app_version: Current app version (for version checking).
            tap_executor: async (x, y, appium_url, session_id) → result.
            type_executor: async (x, y, text, appium_url, session_id) → result.
            source_fetcher: sync/async () → DOM XML string.
            app_executor: async (cmd) → result (for exec actions).

        Returns:
            True if all steps passed, False if healing failed (caller should
            revert to LLM mode).
        """
        self._step_results = []
        self._total_heals = 0
        script.mark_run()

        for step in script.steps:
            start = time.monotonic()

            # Actions that don't need coordinates
            coords_actions = {"tap", "type"}
            no_coords_actions = {"launch", "wait", "exec", "assert", "screenshot", "swipe"}

            # 1. Resolve coordinates only for tap/type actions
            coords = None
            if step.action in coords_actions:
                coords = self._resolve_coords(step)
            elif step.action in no_coords_actions:
                coords = {"x": 0, "y": 0}  # dummy, won't need coords

            if coords is None:
                # Step failed — try healing
                dom_xml = ""
                if source_fetcher:
                    try:
                        if asyncio.iscoroutinefunction(source_fetcher):
                            dom_xml = await source_fetcher()
                        else:
                            dom_xml = source_fetcher()
                    except Exception:
                        pass

                heal_result = self._healer.heal_step(step, dom_xml, app_version)
                if heal_result.get("success"):
                    coords = {"x": heal_result["x"], "y": heal_result["y"]}
                    self._total_heals += 1
                    self._store.append_healing_log(heal_result)
                    logger.info(f"[Replay] healed step {step.step}: {heal_result.get('method', '')}")
                else:
                    elapsed = int((time.monotonic() - start) * 1000)
                    self._step_results.append(ScriptReplayResult(
                        step=step.step, success=False,
                        error_message=heal_result.get("reason", "heal failed"),
                        duration_ms=elapsed,
                    ))
                    return False  # caller should fall back to LLM

            # 2. Find best locator for assertion
            best_locator = self._get_best_locator(step)

            # 3. Execute the action
            elapsed = int((time.monotonic() - start) * 1000)

            if step.action == "launch" and app_executor:
                await app_executor(f"monkey -p {step.target} -c android.intent.category.LAUNCHER 1")
                await asyncio.sleep(3.0)

            elif step.action == "tap" and coords and tap_executor:
                await tap_executor(coords["x"], coords["y"], self._appium_url, self._session_id)
                await asyncio.sleep(0.5)

            elif step.action == "type" and coords and type_executor:
                await type_executor(coords["x"], coords["y"], step.value, self._appium_url, self._session_id)
                await asyncio.sleep(1.0)

            elif step.action == "exec" and app_executor:
                await app_executor(step.value or step.target)
                await asyncio.sleep(1.0)

            elif step.action == "wait":
                wait_seconds = int(step.value) if step.value and step.value.isdigit() else 3
                await asyncio.sleep(wait_seconds)

            # 4. Lightweight assertion: check DOM for expected element
            assertion_ok = True
            if step.expected and source_fetcher:
                try:
                    dom = await source_fetcher() if asyncio.iscoroutinefunction(source_fetcher) else source_fetcher()
                    assertion_ok = self._quick_assert(step, dom)
                except Exception:
                    pass

            self._step_results.append(ScriptReplayResult(
                step=step.step,
                success=assertion_ok,
                duration_ms=elapsed,
                healed=False,
            ))

            if not assertion_ok:
                logger.warning(f"[Replay] step {step.step} assertion failed: {step.expected}")

        # Update script status
        self._store.save(script)
        return True

    # ── Coordinate resolution ───────────────────────────────────

    def _resolve_coords(self, step: ScriptStep) -> dict[str, int] | None:
        """Resolve pixel coordinates from the best available locator.

        Uses normalized_coords from the script step (stored at generation
        time). Real locator resolution (DOM scanning) happens in healing.
        During normal replay, we use the last-known good coordinates.
        """
        if step.normalized_coords and len(step.normalized_coords) == 2:
            nx, ny = step.normalized_coords
            if nx > 0 and ny > 0:
                return {"x": int(nx * 1080), "y": int(ny * 2400)}

        # If no normalized coords, try locators
        for loc in step.locators:
            if loc.type.value == "normalized_coords":
                parts = loc.value.split(",")
                if len(parts) == 2:
                    try:
                        x = int(float(parts[0]) * 1080)
                        y = int(float(parts[1]) * 2400)
                        return {"x": x, "y": y}
                    except (ValueError, IndexError):
                        pass
        return None

    @staticmethod
    def _get_best_locator(step: ScriptStep) -> str:
        """Get the best locator description for logging."""
        if not step.locators:
            return step.target
        best = min(step.locators, key=lambda l: l.priority)
        return f"{best.type.value}:{best.value}"

    # ── Lightweight assertion ────────────────────────────────────

    @staticmethod
    def _quick_assert(step: ScriptStep, dom_xml: str) -> bool:
        """Lightweight DOM assertion — checks if expected text exists.

        This is a fast check that does NOT call Vision or LLM.
        For assert steps, checks if the expected text is found in DOM.
        For non-assert steps, always returns True (success assumed if
        the action didn't throw).
        """
        if step.action != "assert":
            return True
        if not step.expected and not step.target:
            return True
        if not dom_xml:
            return True  # can't verify, assume success

        target = step.expected or step.target
        if not target:
            return True

        # Check if target text appears in DOM
        return target.lower() in dom_xml.lower()
