"""UI tree parser tests for the AppExplorer feature."""
from __future__ import annotations

from testagent.exploration.ui_tree_parser import UIElement, parse_ui_tree, _classify_element


# --- Fixtures ---

SAMPLE_XML = """\
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout"
        package="tv.danmaku.bili" content-desc="" checkable="false" checked="false"
        clickable="true" enabled="true" focusable="true" focused="false"
        scrollable="false" long-clickable="false" password="false" selected="false"
        bounds="[0,0][1080,2340]">
    <node index="0" text="首页" resource-id="tv.danmaku.bili:id/tab_title"
          class="android.widget.TextView" content-desc="首页Tab"
          clickable="true" enabled="true" bounds="[0,2196][270,2340]">
    </node>
    <node index="1" text="热门" resource-id="tv.danmaku.bili:id/tab_title"
          class="android.widget.TextView" content-desc="热门Tab"
          clickable="true" enabled="true" bounds="[270,2196][540,2340]">
    </node>
    <node index="2" text="" resource-id="tv.danmaku.bili:id/search"
          class="android.widget.EditText"
          clickable="false" enabled="true" bounds="[100,50][980,130]">
    </node>
    <node index="3" text="不可用按钮" resource-id="tv.danmaku.bili:id/disabled_btn"
          class="android.widget.Button"
          clickable="true" enabled="false" bounds="[0,100][200,200]">
    </node>
    <node index="4" text="密码输入" resource-id="tv.danmaku.bili:id/password_field"
          class="android.widget.EditText"
          clickable="true" enabled="true" password="true" bounds="[0,200][400,300]">
    </node>
  </node>
</hierarchy>
"""


class TestUIElement:
    """UIElement dataclass tests."""

    def test_center_from_bounds(self):
        """Center coordinates computed correctly from bounds string."""
        elem = UIElement(
            text="test",
            content_desc="",
            element_type="button",
            bounds="[0,0][100,100]",
            resource_id="id/btn",
        )
        assert elem.center_x == 50
        assert elem.center_y == 50

    def test_display_text_prefers_text_over_content_desc(self):
        """display_text returns text when text is non-empty."""
        elem = UIElement(
            text="搜索",
            content_desc="搜索按钮",
            element_type="button",
            bounds="[0,0][100,100]",
            resource_id="id/btn",
        )
        assert elem.display_text == "搜索"

    def test_display_text_falls_back_to_content_desc(self):
        """display_text returns content_desc when text is empty."""
        elem = UIElement(
            text="",
            content_desc="搜索按钮",
            element_type="button",
            bounds="[0,0][100,100]",
            resource_id="id/btn",
        )
        assert elem.display_text == "搜索按钮"

    def test_display_text_empty_when_both_empty(self):
        """display_text returns empty string when both text and content_desc are empty."""
        elem = UIElement(
            text="",
            content_desc="",
            element_type="button",
            bounds="[0,0][100,100]",
            resource_id="id/btn",
        )
        assert elem.display_text == ""


class TestClassifyElement:
    """_classify_element function tests."""

    def test_text_view(self):
        assert _classify_element("android.widget.TextView") == "text_view"

    def test_button(self):
        assert _classify_element("android.widget.Button") == "button"

    def test_image_button(self):
        assert _classify_element("android.widget.ImageButton") == "button"

    def test_edit_text(self):
        assert _classify_element("android.widget.EditText") == "edit_text"

    def test_auto_complete(self):
        assert _classify_element("android.widget.AutoCompleteTextView") == "edit_text"

    def test_checkbox(self):
        assert _classify_element("android.widget.CheckBox") == "checkbox"

    def test_switch(self):
        assert _classify_element("android.widget.Switch") == "switch"

    def test_toggle_button(self):
        assert _classify_element("android.widget.ToggleButton") == "switch"

    def test_recycler_view(self):
        assert _classify_element("androidx.recyclerview.widget.RecyclerView") == "list"

    def test_list_view(self):
        assert _classify_element("android.widget.ListView") == "list"

    def test_unknown_falls_back_to_view(self):
        assert _classify_element("android.widget.RelativeLayout") == "view"


class TestParseUiTree:
    """parse_ui_tree function tests."""

    def test_extracts_clickable_elements(self):
        """Clickable elements with text/desc/resource-id are extracted."""
        elems = parse_ui_tree(SAMPLE_XML)
        texts = [e.display_text for e in elems]
        assert "首页" in texts
        assert "热门" in texts

    def test_excludes_non_clickable(self):
        """Non-clickable, non-editable elements are excluded."""
        xml = """\
        <hierarchy>
          <node text="标签" class="android.widget.TextView"
                clickable="false" enabled="true" bounds="[0,0][100,50]"
                resource-id="id/label"/>
        </hierarchy>
        """
        elems = parse_ui_tree(xml)
        assert len(elems) == 0

    def test_excludes_disabled_elements(self):
        """Disabled elements are excluded even if clickable."""
        elems = parse_ui_tree(SAMPLE_XML)
        texts = [e.display_text for e in elems]
        assert "不可用按钮" not in texts

    def test_extracts_editable_elements(self):
        """EditText elements are extracted even if clickable=false."""
        elems = parse_ui_tree(SAMPLE_XML)
        edit_elems = [e for e in elems if e.element_type == "edit_text"]
        assert len(edit_elems) >= 1
        # The non-password EditText should be present
        assert any(e.resource_id == "tv.danmaku.bili:id/search" for e in edit_elems)

    def test_excludes_password_elements(self):
        """Password fields are excluded."""
        elems = parse_ui_tree(SAMPLE_XML)
        resource_ids = [e.resource_id for e in elems]
        assert "tv.danmaku.bili:id/password_field" not in resource_ids

    def test_dedup_by_text_and_bounds(self):
        """Duplicate elements (same display_text, center_x, center_y) are deduplicated."""
        xml = """\
        <hierarchy>
          <node text="确定" class="android.widget.Button"
                clickable="true" enabled="true" bounds="[0,0][100,50]"
                resource-id="id/btn1"/>
          <node text="确定" class="android.widget.Button"
                clickable="true" enabled="true" bounds="[0,0][100,50]"
                resource-id="id/btn2"/>
        </hierarchy>
        """
        elems = parse_ui_tree(xml)
        assert len(elems) == 1

    def test_limits_to_max_elements(self):
        """At most max_elements are returned."""
        # Build XML with 20 clickable elements
        nodes = []
        for i in range(20):
            nodes.append(
                f'<node text="btn{i}" class="android.widget.Button" '
                f'clickable="true" enabled="true" bounds="[{i*10},0][{i*10+50},50]" '
                f'resource-id="id/btn{i}"/>'
            )
        xml = f'<hierarchy>{"".join(nodes)}</hierarchy>'
        elems = parse_ui_tree(xml, max_elements=15)
        assert len(elems) <= 15

    def test_returns_empty_on_invalid_xml(self):
        """Invalid XML returns empty list."""
        assert parse_ui_tree("not xml at all") == []

    def test_returns_empty_on_empty_source(self):
        """Empty string returns empty list."""
        assert parse_ui_tree("") == []

    def test_element_properties_from_fixture(self):
        """Elements parsed from fixture have correct properties."""
        elems = parse_ui_tree(SAMPLE_XML)
        # Find the 首页 element
        home = next((e for e in elems if e.display_text == "首页"), None)
        assert home is not None
        assert home.text == "首页"
        assert home.content_desc == "首页Tab"
        assert home.element_type == "text_view"
        assert home.bounds == "[0,2196][270,2340]"
        assert home.resource_id == "tv.danmaku.bili:id/tab_title"
        assert home.center_x == 135  # (0+270)//2
        assert home.center_y == 2268  # (2196+2340)//2
