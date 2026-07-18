"""Local vision system type definitions.

Mirrors mobile_vision's data models (PageContext, Rect, UIElement, TextElement)
while following TestAgent's Pydantic / dataclass conventions.
"""
from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ElementSource(str, Enum):
    """Element finding strategy selection.

    - ``multimodal``: Send screenshot to vision API (existing behaviour).
    - ``yolo``: Local YOLOv8 + OCR → structured JSON → LLM → coordinates.
    - ``yolo_with_dom``: DOM-first, YOLO+OCR fallback, LLM decision.
    """

    MULTIMODAL = "multimodal"
    YOLO = "yolo"
    YOLO_WITH_DOM = "yolo_with_dom"


class BBox(BaseModel):
    """Bounding box in pixel coordinates."""

    x1: int
    y1: int
    x2: int
    y2: int

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    @property
    def center_x(self) -> int:
        return (self.x1 + self.x2) // 2

    @property
    def center_y(self) -> int:
        return (self.y1 + self.y2) // 2


class VisualElement(BaseModel):
    """A single UI element detected by YOLO, extracted from DOM, or recognised by OCR.

    Mirrors ``automation_agent/types.py``: ``UIElement`` + ``TextElement``.
    """

    id: str = ""
    type: str = "unknown"
    bbox: BBox = Field(default_factory=lambda: BBox(x1=0, y1=0, x2=0, y2=0))
    confidence: float = 0.0

    # OCR / text fields
    text: str = ""

    # Color analysis fields (visual channel)
    color: str = ""
    color_brightness: float = 0.0

    # DOM-native attributes (DOM channel, 100% accurate)
    clickable: bool = False
    enabled: bool = True
    checked: bool = False
    selected: bool = False
    focused: bool = False
    scrollable: bool = False
    resource_id: str = ""
    content_desc: str = ""

    # Hierarchy
    children: list[VisualElement] = Field(default_factory=list)
    parent_id: str = ""


class PageStructure(BaseModel):
    """Structured page representation sent to the LLM instead of an image.

    Mirrors the ``structured_elements`` JSON shown in the MobileVision article:

    .. code-block:: json

        {
          "page_width": 720,
          "page_height": 1560,
          "source": "visual",
          "elements": [
            {
              "id": "elem_2",
              "type": "关闭小程序按钮",
              "bbox": [643, 61, 685, 102],
              "bbox_center": {"center_x": 664, "center_y": 81},
              "confidence": 0.93
            }
          ]
        }
    """

    page_width: int
    page_height: int
    source: str = "visual"  # "dom" | "visual" | "hybrid"
    elements: list[VisualElement] = Field(default_factory=list)

    # ── helpers ─────────────────────────────────────────────────────

    def to_compact_dict(self) -> dict[str, Any]:
        """Serialize to the compact MobileVision JSON format for LLM consumption.

        Returns a dict matching the article's structured format (list-based bbox
        + explicit bbox_center) for backward-compatible prompting.
        """
        elem_list: list[dict[str, Any]] = []
        for el in self.elements:
            item: dict[str, Any] = {
                "id": el.id,
                "type": el.type,
                "bbox": [el.bbox.x1, el.bbox.y1, el.bbox.x2, el.bbox.y2],
                "bbox_center": {"center_x": el.bbox.center_x, "center_y": el.bbox.center_y},
                "confidence": round(el.confidence, 2),
            }
            if el.text:
                item["text"] = el.text
            if el.color:
                item["color"] = el.color
                item["color_brightness"] = round(el.color_brightness, 1)
            if el.clickable:
                item["clickable"] = True
            if not el.enabled:
                item["enabled"] = False
            if el.checked:
                item["checked"] = True
            if el.selected:
                item["selected"] = True
            if el.children:
                children_list: list[dict[str, Any]] = []
                for c in el.children:
                    child: dict[str, Any] = {
                        "id": c.id,
                        "type": c.type,
                        "bbox": [c.bbox.x1, c.bbox.y1, c.bbox.x2, c.bbox.y2],
                        "bbox_center": {"center_x": c.bbox.center_x, "center_y": c.bbox.center_y},
                        "confidence": round(c.confidence, 2),
                    }
                    if c.text:
                        child["text"] = c.text
                    if c.color:
                        child["color"] = c.color
                    children_list.append(child)
                item["children"] = children_list
            elem_list.append(item)

        return {
            "page_width": self.page_width,
            "page_height": self.page_height,
            "elements": elem_list,
        }

    def to_llm_context(self) -> str:
        """Return a compact JSON string (~200-500 tokens) for LLM consumption."""
        return json.dumps(self.to_compact_dict(), ensure_ascii=False, indent=2)

    def to_integrated_dict(self) -> dict[str, Any]:
        """Serialise into the combined DOM+visual / structured format.

        This adds the element count and source metadata on top of the compact
        format, making it suitable for debugging / monitoring.
        """
        data = self.to_compact_dict()
        data["source"] = self.source
        data["element_count"] = len(self.elements)
        return data
