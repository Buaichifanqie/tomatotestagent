from __future__ import annotations

import pytest

from testagent.plan.ui_scanner import (
    UIElement,
    UIScanResult,
    _filter_dynamic_content,
    format_ui_context,
)


class TestFilterDynamicContent:
    """_filter_dynamic_content filters out transient content."""

    def test_keeps_element_with_resource_id(self) -> None:
        el = UIElement(
            text="推荐",
            resource_id="tv.danmaku.bili:id/tab_title",
            clickable=True,
        )
        result = _filter_dynamic_content([el])
        assert result == [el]

    def test_keeps_short_clickable_text(self) -> None:
        el = UIElement(text="热门", clickable=True)
        result = _filter_dynamic_content([el])
        assert result == [el]

    def test_keeps_short_text_with_content_desc(self) -> None:
        el = UIElement(text="搜索", content_desc="Search button")
        result = _filter_dynamic_content([el])
        assert result == [el]

    def test_filters_long_text_without_resource_id(self) -> None:
        el = UIElement(
            text="狂魔哥解说今天又干了什么惊天动地的事情",
            clickable=False,
        )
        result = _filter_dynamic_content([el])
        assert result == []

    def test_filters_long_text_not_clickable(self) -> None:
        el = UIElement(
            text="这个视频真的太有意思了哈哈哈",
            clickable=False,
        )
        result = _filter_dynamic_content([el])
        assert result == []

    def test_filters_ad_patterns(self) -> None:
        el = UIElement(text="立即购买", clickable=True)
        result = _filter_dynamic_content([el])
        assert result == []

    def test_skips_system_android_resource_id(self) -> None:
        el = UIElement(text="content", resource_id="android:id/content")
        result = _filter_dynamic_content([el])
        assert result == []

    def test_keeps_short_text_without_resource_id(self) -> None:
        el = UIElement(text="我的", clickable=False)
        result = _filter_dynamic_content([el])
        assert result == [el]

    def test_empty_input_returns_empty(self) -> None:
        assert _filter_dynamic_content([]) == []


class TestFormatUiContext:
    """format_ui_context produces structured LLM prompt block."""

    def test_returns_empty_when_no_elements(self) -> None:
        result = UIScanResult(elements=[])
        assert format_ui_context(result) == ""

    def test_separates_clickable_and_reference(self) -> None:
        elements = [
            UIElement(
                text="推荐",
                resource_id="tv.danmaku.bili:id/tab",
                clickable=True,
            ),
            UIElement(
                text="狂魔哥解说今天开箱直播精彩回放看起来真有意思",
                clickable=False,
            ),
            UIElement(text="我的", clickable=False),
        ]
        result = UIScanResult(elements=elements)
        output = format_ui_context(result)
        assert "Clickable Elements" in output
        assert "推荐" in output
        assert "狂魔" not in output
