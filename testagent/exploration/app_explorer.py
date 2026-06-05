"""AppExplorer core orchestration module.

Coordinates the full exploration flow: session management, page navigation,
UI tree parsing, keyword matching, vision fallback, anti-loop detection,
and breadcrumb tracking.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

from testagent.exploration.exploration_planner import ExplorationPlanner, ExplorationTarget
from testagent.exploration.ui_context_map import ElementInfo, PageInfo, UIContextMap
from testagent.exploration.ui_tree_parser import UIElement, parse_ui_tree

__all__ = ["AppExplorer", "_page_fingerprint"]

_log = logging.getLogger(__name__)


def _page_fingerprint(elements: list[UIElement]) -> str:
    """Compute a stable MD5 fingerprint for a page's element set.

    Hashes sorted ``"{text}|{content_desc}"`` for all elements that have
    text or content-desc.  Returns the first 12 hex characters, or ``""``
    when the element list is empty.
    """
    if not elements:
        return ""
    parts = sorted(f"{el.text}|{el.content_desc}" for el in elements if el.text or el.content_desc)
    if not parts:
        return ""
    raw = "\n".join(parts).encode("utf-8")
    return hashlib.md5(raw).hexdigest()[:12]


class AppExplorer:
    """Orchestrates mobile app exploration.

    Parameters
    ----------
    session_manager : Any
        A ``SessionManager`` instance (provided by the caller, typically plan.py).
    llm_callable : async (str) -> str, optional
        LLM function used by the internal ``ExplorationPlanner``.
    vision_client : Any, optional
        A ``VolcanoVisionClient`` for vision-based element finding / page description.
    appium_url : str
        Appium server URL.
    """

    def __init__(
        self,
        session_manager: Any,
        llm_callable: Any = None,
        vision_client: Any = None,
        appium_url: str = "http://localhost:4723",
    ) -> None:
        self._session_manager = session_manager
        self._llm_callable = llm_callable
        self._vision_client = vision_client
        self._appium_url = appium_url
        self._planner = ExplorationPlanner(llm_callable) if llm_callable else None
        self._home_fingerprint: str = ""
        self._visit_counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def explore(
        self,
        prd_text: str,
        app_package: str,
        app_activity: str = "",
    ) -> UIContextMap:
        """Run the full exploration flow and return a UIContextMap.

        Degrades gracefully: returns an empty map on planner / session / launch
        failure.
        """
        context_map = UIContextMap()

        # 1. Plan
        if self._planner is None:
            _log.warning("No LLM callable provided; cannot plan exploration")
            return context_map

        try:
            targets = await self._planner.plan(prd_text)
        except Exception:
            _log.warning("Exploration planning failed", exc_info=True)
            return context_map

        # 2. Create session
        session_id = self._session_manager.create_session()
        if session_id is None:
            _log.warning("Failed to create Appium session")
            return context_map

        try:
            # 3. Launch app
            from testagent.mcp_servers.appium_server.tools import app_launch

            launch_result = await app_launch(
                package=app_package,
                activity=app_activity,
                appium_url=self._appium_url,
                session_id=self._session_manager.session_id,
            )
            if "error" in launch_result:
                _log.warning("App launch failed: %s", launch_result["error"])
                return context_map

            # 4. Wait for app to settle
            await asyncio.sleep(3)

            # 5. Record home page
            home_elements = await self._get_current_elements()
            self._home_fingerprint = _page_fingerprint(home_elements)
            home_page = PageInfo(
                name="首页",
                elements=[ElementInfo.from_ui_element(el) for el in home_elements],
                breadcrumb=["App启动"],
            )
            context_map.add_page(home_page)

            # 6. Explore each target
            for target in targets:
                # Navigate back to home first
                await self._navigate_to_home()

                page_info = await self._explore_target(target)
                if page_info is not None:
                    context_map.add_page(page_info)
                else:
                    # Single target failure: record as failed and continue
                    failed_page = PageInfo(
                        name=target.target_name,
                        breadcrumb=["首页"],
                        exploration_status="failed",
                    )
                    context_map.add_page(failed_page)

            # 7. Navigate back to home at the end
            await self._navigate_to_home()

        finally:
            self._session_manager.close_session()

        return context_map

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _explore_target(self, target: ExplorationTarget) -> PageInfo | None:
        """Explore a single target by executing its reach actions.

        Returns a ``PageInfo`` on success, or ``None`` if any action fails.
        """
        breadcrumb: list[str] = ["首页"]

        for action in target.reach_actions:
            success = await self._execute_action(action)
            if not success:
                _log.warning("Action failed for target '%s': %s", target.target_name, action)
                return None

            # Build breadcrumb entry
            action_desc = f"{action.type}: {action.target_hint}"
            breadcrumb.append(action_desc)

            # Wait for UI stability
            await asyncio.sleep(1.5)

        # On the final page: parse elements
        elements = await self._get_current_elements()
        fingerprint = _page_fingerprint(elements)

        # Anti-loop: max 2 visits per fingerprint
        self._visit_counts[fingerprint] = self._visit_counts.get(fingerprint, 0) + 1
        if self._visit_counts[fingerprint] > 2:
            _log.info("Anti-loop triggered for fingerprint %s", fingerprint)
            return None

        description = await self._get_page_description()

        return PageInfo(
            name=target.target_name,
            elements=[ElementInfo.from_ui_element(el) for el in elements],
            breadcrumb=breadcrumb,
            description=description,
        )

    async def _execute_action(self, action: Any) -> bool:
        """Execute a single ReachAction.  Returns True on success."""
        elements = await self._get_current_elements()
        target_el = self._find_element_by_hint(elements, action.target_hint)

        if target_el is None:
            # Try vision fallback
            target_el = await self._find_element_by_vision(action.target_hint)

        if target_el is None:
            _log.warning("Element not found for hint: %s", action.target_hint)
            return False

        if action.type == "tap":
            from testagent.mcp_servers.appium_server.tools import app_tap

            result = await app_tap(
                x=target_el.center_x,
                y=target_el.center_y,
                appium_url=self._appium_url,
                session_id=self._session_manager.session_id,
            )
            return "error" not in result

        if action.type == "type":
            from testagent.mcp_servers.appium_server.tools import app_tap, app_type_text

            # Focus the target element first
            await app_tap(
                x=target_el.center_x,
                y=target_el.center_y,
                appium_url=self._appium_url,
                session_id=self._session_manager.session_id,
            )
            result = await app_type_text(
                text=action.input_value,
                appium_url=self._appium_url,
                session_id=self._session_manager.session_id,
            )
            return "error" not in result

        _log.warning("Unknown action type: %s", action.type)
        return False

    @staticmethod
    def _find_element_by_hint(
        elements: list[UIElement], hint: str
    ) -> UIElement | None:
        """Find an element by hint using layered matching strategy.

        1. Exact match on ``display_text``
        2. Substring match: hint in display_text
        3. Keyword match: any word from hint appears in display_text
        """
        if not hint:
            return None

        hint_lower = hint.lower()
        hint_words = set(hint_lower.split())

        # Exact match
        for el in elements:
            if el.display_text.lower() == hint_lower:
                return el

        # Substring match
        for el in elements:
            if hint_lower in el.display_text.lower():
                return el

        # Keyword match: any word from hint in display_text
        for el in elements:
            display_lower = el.display_text.lower()
            if any(word in display_lower for word in hint_words):
                return el

        return None

    async def _find_element_by_vision(self, hint: str) -> UIElement | None:
        """Use vision client to locate an element by hint.

        Takes a screenshot, asks the vision model for element coordinates,
        and returns a synthetic ``UIElement`` with those coordinates.
        """
        if self._vision_client is None:
            return None

        try:
            from testagent.mcp_servers.appium_server.tools import app_screenshot

            screenshot_result = await app_screenshot(
                appium_url=self._appium_url,
                session_id=self._session_manager.session_id,
            )
            if "error" in screenshot_result:
                return None

            from testagent.mcp_servers.shared_cache import get_screenshot

            screenshot_id = screenshot_result.get("screenshot_id", "")
            b64 = get_screenshot(screenshot_id)
            if not b64:
                return None

            prompt = (
                f"请在截图中找到以下目标元素：{hint}\n"
                "返回JSON格式: {\"found\": true/false, \"center\": {\"x\": <int>, \"y\": <int>}}"
            )
            raw = await self._vision_client.analyze(b64, prompt)

            data = json.loads(raw)
            if not data.get("found"):
                return None

            center = data.get("center", {})
            cx = center.get("x", 0)
            cy = center.get("y", 0)

            return UIElement(
                text=hint,
                content_desc="",
                element_type="view",
                bounds=f"[{cx},{cy}][{cx + 1},{cy + 1}]",
                resource_id="",
            )
        except Exception:
            _log.debug("Vision fallback failed for hint '%s'", hint, exc_info=True)
            return None

    async def _get_current_elements(self) -> list[UIElement]:
        """Fetch and parse the current page's UI tree."""
        from testagent.mcp_servers.appium_server.tools import app_get_source

        source_result = await app_get_source(
            appium_url=self._appium_url,
            session_id=self._session_manager.session_id,
        )
        xml_source = source_result.get("source", "")
        return parse_ui_tree(xml_source)

    async def _get_page_description(self) -> str:
        """Get a one-line description of the current page via vision."""
        if self._vision_client is None:
            return ""

        try:
            from testagent.mcp_servers.appium_server.tools import app_screenshot

            screenshot_result = await app_screenshot(
                appium_url=self._appium_url,
                session_id=self._session_manager.session_id,
            )
            if "error" in screenshot_result:
                return ""

            from testagent.mcp_servers.shared_cache import get_screenshot

            screenshot_id = screenshot_result.get("screenshot_id", "")
            b64 = get_screenshot(screenshot_id)
            if not b64:
                return ""

            prompt = "请用一句话描述当前页面的主要内容和功能。"
            return await self._vision_client.analyze(b64, prompt)
        except Exception:
            _log.debug("Vision description failed", exc_info=True)
            return ""

    async def _navigate_to_home(self) -> bool:
        """Navigate back to the home page by pressing Back repeatedly.

        Tries up to 5 times, checking the page fingerprint after each press.
        Returns True if home is reached (or as fallback after exhausting attempts).
        """
        if not self._home_fingerprint:
            return True

        from testagent.mcp_servers.appium_server.tools import app_exec

        for _ in range(5):
            elements = await self._get_current_elements()
            if _page_fingerprint(elements) == self._home_fingerprint:
                return True

            await app_exec(
                command="input keyevent 4",
                appium_url=self._appium_url,
                session_id=self._session_manager.session_id,
            )
            await asyncio.sleep(1)

        # Fallback: caller can force-launch if needed
        return True
