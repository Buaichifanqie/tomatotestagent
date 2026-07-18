"""Tests for vision_local engine (with mocks)."""
from __future__ import annotations

import pytest

from testagent.vision_local.engine import LocalVisionEngine
from testagent.vision_local.types import BBox, PageStructure, VisualElement


class FakeRecognizer:
    """A mock recognizer that returns predefined page structure."""

    def recognize_from_base64(self, image_base64: str) -> PageStructure:
        return PageStructure(
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
                    id="elem_1",
                    type="搜索框",
                    bbox=BBox(x1=100, y1=200, x2=300, y2=250),
                    confidence=0.88,
                    text="搜索",
                ),
                VisualElement(
                    id="elem_2",
                    type="按钮",
                    bbox=BBox(x1=35, y1=1354, x2=106, y2=1435),
                    confidence=0.81,
                    text="主页",
                    color="green",
                    color_brightness=127.5,
                ),
            ],
        )


FAKE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node class="android.widget.TextView" bounds="[40,50][300,100]"
          text="首页" clickable="true" enabled="true" index="0"/>
    <node class="android.widget.Button" bounds="[40,150][200,200]"
          text="搜索" clickable="true" enabled="true" index="1"/>
    <node class="android.widget.Button" bounds="[40,250][200,300]"
          text="个人" clickable="true" enabled="true" index="2"/>
  </node>
</hierarchy>"""


@pytest.fixture
def engine() -> LocalVisionEngine:
    return LocalVisionEngine(recognizer=FakeRecognizer(), use_dom=True)


class TestLocalVisionEngine:
    async def test_get_page_structure_visual(self, engine: LocalVisionEngine) -> None:
        result = await engine.get_page_structure(
            screenshot_base64="fake_base64",
            source_hint="visual",
        )
        assert result["source"] == "visual"
        assert result["element_count"] == 3
        assert result["page_width"] == 720
        assert result["page_height"] == 1560

    async def test_get_page_structure_dom(self, engine: LocalVisionEngine) -> None:
        result = await engine.get_page_structure(
            dom_xml=FAKE_XML,
            source_hint="dom",
        )
        assert result["source"] == "dom"
        assert result["element_count"] > 0

    async def test_get_page_structure_no_data(self, engine: LocalVisionEngine) -> None:
        result = await engine.get_page_structure(source_hint="visual")
        assert result["source"] == "empty"
        assert result["element_count"] == 0

    async def test_find_element_by_llm_found(self, engine: LocalVisionEngine) -> None:
        page_struct = (await engine.get_page_structure(
            screenshot_base64="fake", source_hint="visual",
        ))

        async def mock_llm(prompt: str) -> str:
            return '{"found": true, "element_id": "elem_1", "x": 200, "y": 225, "reason": "match"}'

        coords = await engine.find_element_by_llm(
            target="搜索",
            page_structure=page_struct,
            llm_callable=mock_llm,
        )
        assert coords is not None
        assert coords["x"] == 200
        assert coords["y"] == 225

    async def test_find_element_by_llm_not_found(self, engine: LocalVisionEngine) -> None:
        page_struct = (await engine.get_page_structure(
            screenshot_base64="fake", source_hint="visual",
        ))

        async def mock_llm(prompt: str) -> str:
            return '{"found": false, "reason": "element not visible"}'

        coords = await engine.find_element_by_llm(
            target="不存在的元素",
            page_structure=page_struct,
            llm_callable=mock_llm,
        )
        assert coords is None

    async def test_find_element_by_llm_invalid_json(self, engine: LocalVisionEngine) -> None:
        page_struct = (await engine.get_page_structure(
            screenshot_base64="fake", source_hint="visual",
        ))

        async def mock_llm(prompt: str) -> str:
            return "not json at all"

        coords = await engine.find_element_by_llm(
            target="搜索",
            page_structure=page_struct,
            llm_callable=mock_llm,
        )
        assert coords is None
