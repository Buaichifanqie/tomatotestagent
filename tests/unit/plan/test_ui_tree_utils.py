"""UI 树清洗与页面哈希计算工具测试."""
from __future__ import annotations

import xml.etree.ElementTree as ET

from testagent.plan.ui_tree_utils import clean_ui_tree, compute_page_hash, get_page_hash_from_source


class TestCleanUiTree:
    """clean_ui_tree 函数测试."""

    def test_extract_basic_attributes(self):
        """提取 class, resource-id, clickable, enabled 属性."""
        xml_str = '<node class="android.widget.Button" resource-id="com.example:id/btn" clickable="true" enabled="true" text="搜索"/>'
        node = ET.fromstring(xml_str)
        result = clean_ui_tree(node)
        assert result["attrs"]["class"] == "android.widget.Button"
        assert result["attrs"]["resource-id"] == "com.example:id/btn"
        assert result["attrs"]["clickable"] == "true"
        assert result["attrs"]["enabled"] == "true"

    def test_remove_dynamic_attributes(self):
        """移除 bounds, index, focused, selected, checked 等动态属性."""
        xml_str = '<node class="android.widget.Button" bounds="[0,0][100,50]" index="2" focused="true" selected="false" checked="true" text="确定"/>'
        node = ET.fromstring(xml_str)
        result = clean_ui_tree(node)
        assert "bounds" not in result["attrs"]
        assert "index" not in result["attrs"]
        assert "focused" not in result["attrs"]
        assert "selected" not in result["attrs"]
        assert "checked" not in result["attrs"]

    def test_clean_text_attributes_remove_numbers(self):
        """清洗 text/content-desc 中的动态数字."""
        xml_str = '<node text="3条未读消息" content-desc="5个通知"/>'
        node = ET.fromstring(xml_str)
        result = clean_ui_tree(node)
        assert result["attrs"]["text"] == "N条未读消息"
        assert result["attrs"]["content-desc"] == "N个通知"

    def test_empty_text_attributes_excluded(self):
        """空文本属性不保留."""
        xml_str = '<node text="" content-desc=""/>'
        node = ET.fromstring(xml_str)
        result = clean_ui_tree(node)
        assert "text" not in result["attrs"]
        assert "content-desc" not in result["attrs"]

    def test_preserve_hierarchy(self):
        """保留层级关系（Parent-Child 结构）."""
        xml_str = """
        <node class="android.widget.LinearLayout" resource-id="root">
            <node class="android.widget.Button" resource-id="btn1" text="确定"/>
            <node class="android.widget.Button" resource-id="btn2" text="取消"/>
        </node>
        """
        node = ET.fromstring(xml_str)
        result = clean_ui_tree(node)
        assert len(result["children"]) == 2
        assert result["children"][0]["attrs"]["resource-id"] == "btn1"
        assert result["children"][1]["attrs"]["resource-id"] == "btn2"

    def test_resource_id_preserves_numbers(self):
        """resource-id 中的数字不替换（结构标识）."""
        xml_str = '<node resource-id="recycler_view_2"/>'
        node = ET.fromstring(xml_str)
        result = clean_ui_tree(node)
        assert result["attrs"]["resource-id"] == "recycler_view_2"


class TestComputePageHash:
    """compute_page_hash 函数测试."""

    def test_same_tree_same_hash(self):
        """相同树结构产生相同哈希."""
        tree = {"attrs": {"class": "Button"}, "children": []}
        assert compute_page_hash(tree) == compute_page_hash(tree)

    def test_different_tree_different_hash(self):
        """不同树结构产生不同哈希."""
        tree1 = {"attrs": {"class": "Button"}, "children": []}
        tree2 = {"attrs": {"class": "TextView"}, "children": []}
        assert compute_page_hash(tree1) != compute_page_hash(tree2)

    def test_hash_length(self):
        """哈希长度为 12 位."""
        tree = {"attrs": {"class": "Button"}, "children": []}
        assert len(compute_page_hash(tree)) == 12


class TestGetPageHashFromSource:
    """get_page_hash_from_source 函数测试."""

    def test_valid_xml(self):
        """有效 XML 返回非空哈希."""
        xml_str = '<hierarchy><node class="Button" text="确定"/></hierarchy>'
        result = get_page_hash_from_source(xml_str)
        assert len(result) == 12

    def test_invalid_xml_returns_empty(self):
        """无效 XML 返回空字符串."""
        assert get_page_hash_from_source("not xml") == ""

    def test_same_source_same_hash(self):
        """相同 XML 产生相同哈希."""
        xml_str = '<hierarchy><node class="Button" text="确定"/></hierarchy>'
        assert get_page_hash_from_source(xml_str) == get_page_hash_from_source(xml_str)

    def test_different_content_different_hash(self):
        """不同内容产生不同哈希."""
        xml1 = '<hierarchy><node class="Button" text="确定"/></hierarchy>'
        xml2 = '<hierarchy><node class="Button" text="取消"/></hierarchy>'
        assert get_page_hash_from_source(xml1) != get_page_hash_from_source(xml2)
