"""Tests for UIContextMap data model — AppExplorer feature."""
from __future__ import annotations

import pytest

from testagent.exploration.ui_context_map import ElementInfo, PageInfo, UIContextMap
from testagent.exploration.ui_tree_parser import UIElement


# --- Helpers ---

def _make_ui_element(
    text: str = "",
    content_desc: str = "",
    element_type: str = "button",
    bounds: str = "[0,0][100,100]",
    resource_id: str = "",
) -> UIElement:
    return UIElement(
        text=text,
        content_desc=content_desc,
        element_type=element_type,
        bounds=bounds,
        resource_id=resource_id,
    )


# --- TestElementInfo ---

class TestElementInfo:

    def test_from_ui_element(self):
        """ElementInfo.from_ui_element uses el.display_text (text) for the text field."""
        el = _make_ui_element(text="登录", content_desc="登录按钮", element_type="button", bounds="[10,20][110,120]")
        info = ElementInfo.from_ui_element(el)
        assert info.text == "登录"
        assert info.element_type == "button"
        assert info.center_x == 60   # (10+110)//2
        assert info.center_y == 70   # (20+120)//2
        assert info.resource_id == ""

    def test_from_ui_element_with_content_desc(self):
        """When text is empty, display_text falls back to content_desc."""
        el = _make_ui_element(text="", content_desc="搜索按钮", element_type="button", bounds="[0,0][200,100]")
        info = ElementInfo.from_ui_element(el)
        assert info.text == "搜索按钮"
        assert info.center_x == 100
        assert info.center_y == 50


# --- TestPageInfo ---

class TestPageInfo:

    def test_create_page_info(self):
        """PageInfo stores fields correctly with defaults."""
        page = PageInfo(
            name="首页",
            elements=[],
            breadcrumb=["App", "首页"],
        )
        assert page.name == "首页"
        assert page.breadcrumb == ["App", "首页"]
        assert page.description == ""
        assert page.exploration_status == "success"

    def test_to_context_string(self):
        """to_context_string formats page info as readable text."""
        elem = ElementInfo(text="登录", element_type="button", center_x=100, center_y=200, resource_id="id/login")
        page = PageInfo(
            name="登录页",
            elements=[elem],
            breadcrumb=["App", "登录页"],
            description="用户登录页面",
        )
        result = page.to_context_string()
        assert "### 登录页" in result
        assert "用户登录页面" in result
        assert "App → 登录页" in result
        assert "登录 [button] (100, 200)" in result


# --- TestUIContextMap ---

class TestUIContextMap:

    def test_empty_map(self):
        """Empty UIContextMap has no pages and zero elements."""
        ctx = UIContextMap()
        assert ctx.pages == []
        assert ctx.element_count == 0

    def test_to_context_string_single_page(self):
        """Single page context string has no separator."""
        elem = ElementInfo(text="确定", element_type="button", center_x=50, center_y=50)
        page = PageInfo(name="弹窗", elements=[elem], breadcrumb=["App", "弹窗"])
        ctx = UIContextMap(pages=[page])
        result = ctx.to_context_string()
        assert "### 弹窗" in result
        assert "---" not in result

    def test_to_context_string_multiple_pages(self):
        """Multiple pages are separated by '---'."""
        page1 = PageInfo(name="首页", elements=[], breadcrumb=["App", "首页"])
        page2 = PageInfo(name="详情", elements=[], breadcrumb=["App", "首页", "详情"])
        ctx = UIContextMap(pages=[page1, page2])
        result = ctx.to_context_string()
        assert "### 首页" in result
        assert "### 详情" in result
        assert "\n\n---\n\n" in result

    def test_to_dict_and_from_dict(self):
        """Round-trip through to_dict/from_dict preserves data."""
        elem = ElementInfo(text="提交", element_type="button", center_x=100, center_y=200, resource_id="id/submit")
        page = PageInfo(
            name="表单页",
            elements=[elem],
            breadcrumb=["App", "表单页"],
            description="填写表单",
        )
        ctx = UIContextMap(pages=[page])
        d = ctx.to_dict()
        restored = UIContextMap.from_dict(d)
        assert len(restored.pages) == 1
        assert restored.pages[0].name == "表单页"
        assert restored.pages[0].elements[0].text == "提交"
        assert restored.pages[0].description == "填写表单"

    def test_element_count(self):
        """element_count sums elements across all pages."""
        elem1 = ElementInfo(text="A", element_type="button", center_x=0, center_y=0)
        elem2 = ElementInfo(text="B", element_type="button", center_x=0, center_y=0)
        elem3 = ElementInfo(text="C", element_type="button", center_x=0, center_y=0)
        page1 = PageInfo(name="P1", elements=[elem1, elem2], breadcrumb=[])
        page2 = PageInfo(name="P2", elements=[elem3], breadcrumb=[])
        ctx = UIContextMap(pages=[page1, page2])
        assert ctx.element_count == 3

    def test_mark_exploration_failed(self):
        """Setting exploration_status to 'failed' is reflected in serialization."""
        page = PageInfo(
            name="失败页",
            elements=[],
            breadcrumb=["App"],
            exploration_status="failed",
        )
        ctx = UIContextMap(pages=[page])
        d = ctx.to_dict()
        restored = UIContextMap.from_dict(d)
        assert restored.pages[0].exploration_status == "failed"
