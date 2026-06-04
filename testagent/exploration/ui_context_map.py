"""UIContextMap data model for the AppExplorer feature.

Stores explored page information and generates prompt injection strings
so the TC generation LLM sees real UI elements.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from testagent.exploration.ui_tree_parser import UIElement


@dataclass
class ElementInfo:
    """Simplified element info for storage in UIContextMap."""

    text: str
    element_type: str
    center_x: int
    center_y: int
    resource_id: str = ""

    @classmethod
    def from_ui_element(cls, el: UIElement) -> ElementInfo:
        """Convert from the parser's UIElement."""
        return cls(
            text=el.display_text,
            element_type=el.element_type,
            center_x=el.center_x,
            center_y=el.center_y,
            resource_id=el.resource_id,
        )

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "element_type": self.element_type,
            "center_x": self.center_x,
            "center_y": self.center_y,
            "resource_id": self.resource_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ElementInfo:
        return cls(
            text=d["text"],
            element_type=d["element_type"],
            center_x=d["center_x"],
            center_y=d["center_y"],
            resource_id=d.get("resource_id", ""),
        )


@dataclass
class PageInfo:
    """A single explored page."""

    name: str
    elements: list[ElementInfo] = field(default_factory=list)
    breadcrumb: list[str] = field(default_factory=list)
    description: str = ""
    exploration_status: str = "success"

    def to_context_string(self) -> str:
        """Format the page as readable text for prompt injection."""
        lines = [f"### {self.name}"]
        if self.description:
            lines.append(self.description)
        if self.breadcrumb:
            lines.append(f"导航路径: {' → '.join(self.breadcrumb)}")
        if self.elements:
            lines.append("可交互元素:")
            for el in self.elements:
                lines.append(f"  - {el.text} [{el.element_type}] ({el.center_x}, {el.center_y})")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "elements": [el.to_dict() for el in self.elements],
            "breadcrumb": self.breadcrumb,
            "description": self.description,
            "exploration_status": self.exploration_status,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PageInfo:
        return cls(
            name=d["name"],
            elements=[ElementInfo.from_dict(el) for el in d.get("elements", [])],
            breadcrumb=d.get("breadcrumb", []),
            description=d.get("description", ""),
            exploration_status=d.get("exploration_status", "success"),
        )


@dataclass
class UIContextMap:
    """Collection of explored pages."""

    pages: list[PageInfo] = field(default_factory=list)

    def add_page(self, page: PageInfo) -> None:
        """Add a page to the map."""
        self.pages.append(page)

    @property
    def element_count(self) -> int:
        """Sum of all elements across pages."""
        return sum(len(page.elements) for page in self.pages)

    def to_context_string(self) -> str:
        """Join all page context strings with separator."""
        return "\n\n---\n\n".join(page.to_context_string() for page in self.pages)

    def to_dict(self) -> dict:
        return {"pages": [page.to_dict() for page in self.pages]}

    @classmethod
    def from_dict(cls, d: dict) -> UIContextMap:
        return cls(pages=[PageInfo.from_dict(p) for p in d.get("pages", [])])
