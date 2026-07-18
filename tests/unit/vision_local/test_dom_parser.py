"""Tests for vision_local DOM parser."""
from __future__ import annotations

from testagent.vision_local.dom_parser import DomParser
from testagent.vision_local.types import BBox


SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node class="android.widget.LinearLayout" bounds="[0,0][1080,2400]">
      <node class="android.widget.TextView" bounds="[40,50][300,100]"
            text="首页" resource-id="com.example:id/title"
            clickable="true" enabled="true"/>
      <node class="android.widget.ImageView" bounds="[900,60][1000,100]"
            content-desc="搜索" clickable="true" enabled="true"/>
      <node class="android.widget.EditText" bounds="[40,150][800,200]"
            text="" clickable="true" enabled="true" focused="true"/>
      <node class="android.widget.Button" bounds="[40,250][100,300]"
            text="登录" clickable="true" enabled="true"/>
      <node class="android.widget.TextView" bounds="[40,400][400,450]"
            text="推荐内容" clickable="false"/>
    </node>
  </node>
</hierarchy>"""


class TestDomParser:
    def test_parse_bounds(self) -> None:
        bbox = DomParser._parse_bounds("[40,50][300,100]")
        assert bbox is not None
        assert bbox.x1 == 40
        assert bbox.y1 == 50
        assert bbox.x2 == 300
        assert bbox.y2 == 100

    def test_parse_bounds_invalid(self) -> None:
        assert DomParser._parse_bounds("") is None
        assert DomParser._parse_bounds("invalid") is None

    def test_parse_xml(self) -> None:
        elements, w, h = DomParser.parse(SAMPLE_XML)
        assert len(elements) >= 5  # at least the 5 meaningful nodes

        text_views = [e for e in elements if e.type == "TextView"]
        assert len(text_views) >= 2
        assert text_views[0].text == "首页"

        btn = [e for e in elements if e.type == "Button"]
        assert len(btn) >= 1
        assert btn[0].clickable is True
        assert btn[0].enabled is True

        edittext = [e for e in elements if e.type == "EditText"]
        assert len(edittext) >= 1
        assert edittext[0].focused is True

    def test_is_system_ui(self) -> None:
        from testagent.vision_local.types import VisualElement

        sys_elem = VisualElement(resource_id="com.android.systemui:id/status_bar")
        normal_elem = VisualElement(resource_id="com.example:id/title")

        assert DomParser._is_system_ui(sys_elem) is True
        assert DomParser._is_system_ui(normal_elem) is False

    def test_is_empty_container(self) -> None:
        from testagent.vision_local.types import VisualElement

        empty = VisualElement(type="FrameLayout")
        with_text = VisualElement(type="FrameLayout", text="hello")
        with_desc = VisualElement(type="FrameLayout", content_desc="desc")

        assert DomParser._is_empty_container(empty) is True
        assert DomParser._is_empty_container(with_text) is False
        assert DomParser._is_empty_container(with_desc) is False

    def test_is_rich_dom(self) -> None:
        from testagent.vision_local.types import VisualElement

        # Rich DOM: >=3 clickable + >=1 text
        rich_elements = [
            VisualElement(type="Button", clickable=True, text="登录"),
            VisualElement(type="TextView", clickable=True, text="首页"),
            VisualElement(type="ImageView", clickable=True, content_desc="搜索"),
            VisualElement(type="Button", clickable=False, text="推荐"),
        ]
        assert DomParser.is_rich_dom(rich_elements) is True

        # Poor DOM: not enough clickable
        poor_elements = [
            VisualElement(type="TextView", clickable=False, text="标题"),
            VisualElement(type="TextView", clickable=False, text="内容"),
        ]
        assert DomParser.is_rich_dom(poor_elements) is False
