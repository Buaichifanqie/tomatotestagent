"""UI tree parser for the AppExplorer feature.

Parses Appium UiAutomator2 XML source into structured UIElement objects,
extracting interactive elements for exploration and context mapping.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


@dataclass
class UIElement:
    """Represents a parsed interactive UI element."""

    text: str
    content_desc: str
    element_type: str
    bounds: str
    resource_id: str
    center_x: int = field(init=False)
    center_y: int = field(init=False)

    def __post_init__(self) -> None:
        self.center_x, self.center_y = _parse_bounds_center(self.bounds)

    @property
    def display_text(self) -> str:
        """Return text if non-empty, else content_desc."""
        return self.text if self.text else self.content_desc


# Editable class names (extracted even if clickable=false)
_EDITABLE_CLASSES = frozenset({
    "android.widget.EditText",
    "android.widget.AutoCompleteTextView",
})

# Bounds parsing pattern: [left,top][right,bottom]
_BOUNDS_PATTERN = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def _parse_bounds_center(bounds: str) -> tuple[int, int]:
    """Parse bounds string and compute center coordinates."""
    match = _BOUNDS_PATTERN.match(bounds)
    if not match:
        return (0, 0)
    left, top, right, bottom = (int(v) for v in match.groups())
    return ((left + right) // 2, (top + bottom) // 2)


def _classify_element(class_name: str) -> str:
    """Map Android class name to a friendly element type string."""
    simple = class_name.rsplit(".", 1)[-1] if "." in class_name else class_name
    mapping = {
        "TextView": "text_view",
        "ImageView": "image_view",
        "Button": "button",
        "ImageButton": "button",
        "EditText": "edit_text",
        "AutoCompleteTextView": "edit_text",
        "CheckBox": "checkbox",
        "Switch": "switch",
        "ToggleButton": "switch",
        "RecyclerView": "list",
        "ListView": "list",
    }
    return mapping.get(simple, "view")


def _is_interactive(node: ET.Element) -> bool:
    """Check whether a node should be extracted as an interactive element."""
    attrs = node.attrib
    # Exclude disabled elements
    if attrs.get("enabled") == "false":
        return False
    # Exclude password fields
    if attrs.get("password") == "true":
        return False
    # Must have some identifying text or resource-id
    text = attrs.get("text", "")
    content_desc = attrs.get("content-desc", "")
    resource_id = attrs.get("resource-id", "")
    if not text and not content_desc and not resource_id:
        return False
    # Interactive if clickable or editable class
    if attrs.get("clickable") == "true":
        return True
    class_name = attrs.get("class", "")
    if class_name in _EDITABLE_CLASSES:
        return True
    return False


def parse_ui_tree(xml_source: str, max_elements: int = 15) -> list[UIElement]:
    """Parse Appium XML source and return interactive UI elements.

    Args:
        xml_source: XML string from appium driver.page_source / app_get_source().
        max_elements: Maximum number of elements to return.

    Returns:
        Deduplicated list of UIElement objects, up to max_elements.
    """
    if not xml_source or not xml_source.strip():
        return []

    try:
        root = ET.fromstring(xml_source)
    except ET.ParseError:
        return []

    seen: set[tuple[str, int, int]] = set()
    elements: list[UIElement] = []

    for node in root.iter("node"):
        if not _is_interactive(node):
            continue

        attrs = node.attrib
        text = attrs.get("text", "")
        content_desc = attrs.get("content-desc", "")
        bounds = attrs.get("bounds", "[0,0][0,0]")
        resource_id = attrs.get("resource-id", "")
        class_name = attrs.get("class", "")
        element_type = _classify_element(class_name)

        elem = UIElement(
            text=text,
            content_desc=content_desc,
            element_type=element_type,
            bounds=bounds,
            resource_id=resource_id,
        )

        # Dedup by (display_text, center_x, center_y)
        dedup_key = (elem.display_text, elem.center_x, elem.center_y)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        elements.append(elem)
        if len(elements) >= max_elements:
            break

    return elements
