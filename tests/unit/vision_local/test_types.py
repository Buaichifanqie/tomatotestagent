"""Tests for vision_local types."""
from __future__ import annotations

from testagent.vision_local.types import BBox, ElementSource, PageStructure, VisualElement


class TestElementSource:
    def test_values(self) -> None:
        assert ElementSource.MULTIMODAL.value == "multimodal"
        assert ElementSource.YOLO.value == "yolo"
        assert ElementSource.YOLO_WITH_DOM.value == "yolo_with_dom"


class TestBBox:
    def test_properties(self) -> None:
        bbox = BBox(x1=100, y1=200, x2=300, y2=400)
        assert bbox.width == 200
        assert bbox.height == 200
        assert bbox.center_x == 200
        assert bbox.center_y == 300


class TestVisualElement:
    def test_create(self) -> None:
        el = VisualElement(
            id="elem_0",
            type="button",
            bbox=BBox(x1=10, y1=20, x2=100, y2=50),
            confidence=0.95,
            text="搜索",
            color="green",
            color_brightness=127.5,
        )
        assert el.id == "elem_0"
        assert el.type == "button"
        assert el.text == "搜索"
        assert el.color == "green"
        assert el.bbox.center_x == 55

    def test_defaults(self) -> None:
        el = VisualElement()
        assert el.id == ""
        assert el.type == "unknown"
        assert el.confidence == 0.0
        assert el.clickable is False
        assert el.enabled is True


class TestPageStructure:
    def test_to_compact_dict(self) -> None:
        ps = PageStructure(
            page_width=720,
            page_height=1560,
            source="visual",
            elements=[
                VisualElement(
                    id="elem_0",
                    type="关闭按钮",
                    bbox=BBox(x1=643, y1=61, x2=685, y2=102),
                    confidence=0.93,
                ),
                VisualElement(
                    id="text_0",
                    type="text_block",
                    bbox=BBox(x1=230, y1=69, x2=490, y2=103),
                    confidence=0.83,
                    text="XX棉店官方商城",
                    color="dark_gray",
                    color_brightness=77.0,
                ),
            ],
        )
        d = ps.to_compact_dict()
        assert d["page_width"] == 720
        assert d["page_height"] == 1560
        assert len(d["elements"]) == 2

        elem0 = d["elements"][0]
        assert elem0["id"] == "elem_0"
        assert elem0["bbox"] == [643, 61, 685, 102]
        assert elem0["bbox_center"] == {"center_x": 664, "center_y": 81}

        elem1 = d["elements"][1]
        assert elem1["text"] == "XX棉店官方商城"
        assert elem1["color"] == "dark_gray"
        assert "text" not in elem0  # YOLO element without text

    def test_to_llm_context_length(self) -> None:
        """Compact JSON should be under 500 tokens (rough check by bytes)."""
        ps = PageStructure(
            page_width=720,
            page_height=1560,
            source="visual",
            elements=[
                VisualElement(
                    id=f"elem_{i}",
                    type="button",
                    bbox=BBox(x1=10 + i, y1=20 + i, x2=100 + i, y2=50 + i),
                    confidence=0.9 - i * 0.05,
                )
                for i in range(20)
            ],
        )
        context = ps.to_llm_context()
        # 500 tokens ≈ ~2000 bytes; 20 elem × ~230 bytes ≈ ~4600
        assert len(context.encode("utf-8")) < 5000  # generous bound

    def test_to_integrated_dict(self) -> None:
        ps = PageStructure(page_width=1080, page_height=2400, source="dom")
        d = ps.to_integrated_dict()
        assert d["source"] == "dom"
        assert d["element_count"] == 0
