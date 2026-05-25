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


def _filter_dynamic_content(elements: list[UIElement]) -> list[UIElement]:
    """Filter out transient/dynamic content, keep structural UI elements.

    Dynamic content (video titles, usernames, descriptions, counts) changes
    on every app launch — using them as tap targets guarantees flaky tests.
    This function keeps only structural elements that are stable across runs.

    Keep rules (any one is sufficient):
    1. Has a non-generic resource-id → layout control
    2. Short text (≤ 15) + clickable → button/tab
    3. Short text (≤ 15) + content-desc → accessible control

    Filter out:
    1. No resource-id, not clickable, text > 12 → likely dynamic content
    2. Text > 20 chars → definitely a title/description
    3. Generic Android resource-ids like ``android:id/content``
    """
    result: list[UIElement] = []
    for el in elements:
        rid = (el.resource_id or "").strip()

        # ── Always keep elements with a real resource-id ──────────
        if rid and ":id/" in rid:
            short = rid.split(":id/", 1)[1]
            # Skip generic Android system IDs
            if short and short != "content" and not short.startswith("android:"):
                result.append(el)
                continue

        # ── Keep short clickable elements (buttons, tabs, nav items) ──
        if el.clickable and len(el.text) <= 15:
            result.append(el)
            continue

        # ── Keep short elements with accessibility labels ──
        if el.content_desc and len(el.text) <= 15:
            result.append(el)
            continue

        # ── Filter out long text without resource-id ──
        if not rid and len(el.text) > 12:
            continue

        # ── Keep other short text elements ──
        if len(el.text) <= 12:
            result.append(el)

    return result


def format_ui_context(result: UIScanResult, max_elements: int = 80) -> str:
    """Format discovered UI elements into a structured block for the LLM prompt.

    Deduplicates elements by (text, resource_id), filters out dynamic/transient
    content (video titles, usernames), separates clickable structural elements
    from reference-only labels, and produces a markdown table.

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

    # Filter out dynamic content so the LLM only sees stable structural elements
    unique = _filter_dynamic_content(unique)

    # Sort: clickable first, then by text length ascending
    unique.sort(key=lambda e: (not e.clickable, len(e.text)))

    # Truncate
    unique = unique[:max_elements]

    # Separate clickable (safe for tap) from reference-only elements.
    # Elements without readable text (empty text + code-like resource ID)
    # are excluded from the clickable list — they can't be tapped via text
    # selector and would cause the LLM to hallucinate invalid tap targets
    # like "expand_search".
    clickable_els = [
        el for el in unique
        if el.clickable and el.text.strip()
    ]
    reference_els = [
        el for el in unique
        if not (el.clickable and el.text.strip())
    ]

    # ── Clickable elements table (safe for tap targets) ──────────
    clickable_rows: list[str] = []
    for el in clickable_els:
        text_cell = _truncate(el.text, _TRUNCATE_TEXT_AT) or "--"
        rid_cell = _short_rid(el.resource_id) or "--"
        clickable_rows.append(f"| {text_cell} | {rid_cell} |")

    # ── Reference labels table (visible text, NOT safe for tap) ──
    ref_rows: list[str] = []
    for el in reference_els:
        text_cell = _truncate(el.text, _TRUNCATE_TEXT_AT) or "--"
        rid_cell = _short_rid(el.resource_id) or "--"
        ref_rows.append(f"| {text_cell} | {rid_cell} |")

    # Build comma-separated safe clickable text labels
    safe_labels = [el.text for el in clickable_els if el.text and len(el.text) < 30]
    safe_labels_str = ", ".join(f'"{l}"' for l in safe_labels)

    parts = [
        "\n\n## Real UI Elements Detected on Device\n",
        "Below are real UI elements found on screen. "
        "Only use **Clickable Elements** for `tap` targets. "
        "The reference labels are visible but should NOT be used as tap targets.\n",
    ]

    if clickable_rows:
        parts.append("### Clickable Elements (safe for tap targets)\n")
        parts.append("| Text | Resource ID |\n|---|---|\n")
        parts.append("\n".join(clickable_rows))
        parts.append("\n")

    if reference_els:
        parts.append("### Other Visible Labels (reference only — do NOT use as tap targets)\n")
        parts.append("| Text | Resource ID |\n|---|---|\n")
        parts.append("\n".join(ref_rows))
        parts.append("\n")

    if safe_labels:
        parts.append(
            "**IMPORTANT:** All `tap` targets MUST use one of the "
            "Clickable Elements text values. Do NOT use reference labels or "
            "invent UI labels for tap actions.\n"
        )

    return "\n".join(parts)


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
