from __future__ import annotations

import asyncio
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from testagent.mcp_servers.appium_server.tools import (
    app_get_source,
    app_launch,
    app_tap,
)
from testagent.plan.popup_handler import PopupHandler
from testagent.plan.session_manager import SessionManager


@dataclass
class UIElement:
    """Represents a single discovered node from the device UI hierarchy."""

    text: str = ""
    content_desc: str = ""
    resource_id: str = ""
    class_name: str = ""
    clickable: bool = False
    enabled: bool = False
    package: str = ""
    bounds: str = ""


@dataclass
class UIScanResult:
    """Result of a UI discovery scan."""

    elements: list[UIElement] = field(default_factory=list)
    scan_duration_ms: int = 0


def _parse_ui_elements(xml_str: str) -> list[UIElement]:
    """Parse UI hierarchy XML into a list of UIElement objects.

    Extracts all ``<node>`` elements that have at least one of: text,
    content-desc, or resource-id. Purely structural containers (no text,
    no description, no identifier) are skipped.

    Args:
        xml_str: The XML page source returned by Appium.

    Returns:
        A list of UIElement objects. Empty on parse failure.
    """
    if not xml_str:
        return []

    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        return []

    elements: list[UIElement] = []

    def _walk(node: ET.Element) -> None:
        attrs = node.attrib
        text = (attrs.get("text") or "").strip()
        desc = (attrs.get("content-desc") or "").strip()
        rid = (attrs.get("resource-id") or "").strip()

        if text or desc or rid:
            elements.append(
                UIElement(
                    text=text,
                    content_desc=desc,
                    resource_id=rid,
                    class_name=attrs.get("class", ""),
                    clickable=attrs.get("clickable") == "true",
                    enabled=attrs.get("enabled") == "true",
                    package=attrs.get("package", ""),
                    bounds=attrs.get("bounds", ""),
                )
            )

        for child in node:
            _walk(child)

    _walk(root)
    return elements


def _short_rid(rid: str) -> str:
    """Shorten a resource-id to its last segment after ``:id/``."""
    if ":id/" in rid:
        return rid.split(":id/", 1)[1]
    return rid


_TRUNCATE_TEXT_AT = 45
_TRUNCATE_DESC_AT = 35


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis if longer than max_len."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def format_ui_context(result: UIScanResult, max_elements: int = 80) -> str:
    """Format discovered UI elements into a structured block for the LLM prompt.

    Deduplicates elements by (text, resource_id), sorts clickable elements
    first, truncates to ``max_elements``, and produces both a markdown table
    and a quick-scan text label list.

    Args:
        result: The scan result to format.
        max_elements: Maximum number of elements to include.

    Returns:
        A formatted string block, or an empty string if there are no elements.
    """
    if not result.elements:
        return ""

    # Deduplicate by (text, resource_id)
    seen: set[tuple[str, str]] = set()
    unique: list[UIElement] = []
    for el in result.elements:
        key = (el.text, el.resource_id)
        if key not in seen:
            seen.add(key)
            unique.append(el)

    # Sort: clickable first, then by text length ascending
    unique.sort(key=lambda e: (not e.clickable, len(e.text)))

    # Truncate
    unique = unique[:max_elements]

    # Build markdown table rows
    rows: list[str] = []
    for el in unique:
        text_cell = _truncate(el.text, _TRUNCATE_TEXT_AT) or "--"
        desc_cell = _truncate(el.content_desc, _TRUNCATE_DESC_AT) or "--"
        rid_cell = _short_rid(el.resource_id) or "--"
        clickable_cell = "Yes" if el.clickable else ""
        rows.append(f"| {text_cell} | {desc_cell} | {rid_cell} | {clickable_cell} |")

    rows_str = "\n".join(rows)

    # Build comma-separated text labels for quick LLM scanning
    labels = [el.text for el in unique if el.text and len(el.text) < 30]
    labels_str = ", ".join(f'"{l}"' for l in labels)

    return (
        "\n\n## Real UI Elements Detected on Device\n\n"
        "The following real UI elements were found on the current screen. "
        "Use these EXACT values when writing test step targets.\n\n"
        "| Text | Content Desc | Resource ID | Clickable |\n"
        "|---|---|---|---|\n"
        f"{rows_str}\n\n"
        "### Element Text Labels (use these for tap/assert targets)\n"
        f"{labels_str}\n\n"
        "**IMPORTANT:** All `assert` and `tap` targets MUST use one of the "
        "text values from the table above. Do NOT invent UI labels.\n"
    )


def _close_session(session_id: str | None, appium_url: str) -> None:
    """Close an Appium session synchronously.

    Sends an HTTP DELETE to terminate the session. Swallows all exceptions.
    """
    if not session_id:
        return
    import httpx

    try:
        with httpx.Client(timeout=10) as client:
            client.delete(f"{appium_url}/session/{session_id}")
    except Exception:
        pass


def discover_ui_elements(
    package: str,
    activity: str = "",
    appium_url: str = "http://localhost:4723",
    scan_timeout: int = 60,
) -> UIScanResult | None:
    """Launch the app, dismiss popups, and discover visible UI elements.

    Creates a temporary Appium session, launches the app, iteratively
    dismisses popups, captures the UI hierarchy XML, and parses it into
    a ``UIScanResult``. The session is always closed on return.

    This is a best-effort enhancement. If anything fails (Appium not
    running, device not connected, app crash), the function returns None
    and the pipeline continues without UI context.

    Args:
        package: Android app package name.
        activity: Optional Android launch activity.
        appium_url: Appium server URL.
        scan_timeout: Wall-clock timeout in seconds for the entire scan.

    Returns:
        A ``UIScanResult`` with discovered elements, or None on failure.
    """
    start = time.monotonic()
    session_mgr = SessionManager(appium_url=appium_url)

    sid = session_mgr.create_session()
    if not sid:
        return None

    try:
        # ── Launch the app ────────────────────────────────────────
        launch_result = asyncio.run(
            app_launch(
                package=package,
                activity=activity,
                appium_url=appium_url,
                session_id=sid,
            )
        )
        if launch_result.get("error"):
            return None

        elapsed = time.monotonic() - start
        if elapsed * 1000 >= scan_timeout * 1000:
            return None

        time.sleep(2)  # initial render

        # ── Dismiss cascading popups ──────────────────────────────
        popup_handler = PopupHandler()
        for _round in range(10):
            if (time.monotonic() - start) * 1000 >= scan_timeout * 1000:
                break

            src = asyncio.run(
                app_get_source(
                    appium_url=appium_url,
                    session_id=sid,
                )
            )
            xml = src.get("source", "")
            if not xml:
                break

            popup = popup_handler.handle(xml)
            if not popup:
                break

            button_text = popup.get("button_text", "")
            if not button_text:
                break

            tap_result = asyncio.run(
                app_tap(
                    selector=button_text,
                    strategy="uiautomator",
                    appium_url=appium_url,
                    session_id=sid,
                )
            )
            if tap_result.get("error"):
                break
            time.sleep(1)

        # ── Capture clean UI hierarchy ────────────────────────────
        final_src = asyncio.run(
            app_get_source(
                appium_url=appium_url,
                session_id=sid,
            )
        )
        final_xml = final_src.get("source", "")
        if not final_xml:
            return None

        elements = _parse_ui_elements(final_xml)
        elapsed_ms = int((time.monotonic() - start) * 1000)

        return UIScanResult(elements=elements, scan_duration_ms=elapsed_ms)

    finally:
        _close_session(sid, appium_url)
