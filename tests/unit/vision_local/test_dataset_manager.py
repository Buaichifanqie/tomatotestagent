"""Tests for vision_local dataset manager (auto-label)."""
from __future__ import annotations

from testagent.vision_local.dataset_manager import DatasetManager

SAMPLE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<hierarchy rotation="0">
  <node class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node class="android.widget.LinearLayout" bounds="[0,0][1080,2400]">
      <node class="android.widget.TextView" bounds="[40,50][300,100]"
            text="首页" clickable="true" enabled="true" index="0"/>
      <node class="android.widget.ImageView" bounds="[900,60][1000,100]"
            content-desc="搜索" clickable="true" enabled="true" index="1"/>
      <node class="android.widget.EditText" bounds="[40,150][800,200]"
            clickable="true" enabled="true" focused="true" index="2"/>
      <node class="android.widget.Button" bounds="[40,250][100,300]"
            text="登录" clickable="true" enabled="true" index="3"/>
    </node>
  </node>
</hierarchy>"""


class TestAutoLabel:
    def test_auto_label_from_dom_basic(self) -> None:
        """基本自动标注功能。"""
        label, metadata = DatasetManager.auto_label_from_dom(
            dom_xml=SAMPLE_XML,
            image_width=1080,
            image_height=2400,
        )
        assert label.strip(), "应该有标注结果"
        lines = label.strip().split("\n")
        assert len(lines) >= 3, f"应该有3+个标注，实际: {len(lines)}"
        assert len(metadata) >= 3, f"应该有3+个元数据，实际: {len(metadata)}"

        # 验证 YOLO 格式: class_id cx cy w h (均为归一化)
        for line in lines:
            parts = line.strip().split()
            assert len(parts) == 5, f"格式错误: {line}"
            cls_id = int(parts[0])
            cx, cy, w, h = float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4])
            assert 0 <= cls_id <= 10, f"类别ID异常: {cls_id}"
            assert 0 <= cx <= 1, f"cx越界: {cx}"
            assert 0 <= cy <= 1, f"cy越界: {cy}"
            assert 0 < w <= 1, f"w越界: {w}"
            assert 0 < h <= 1, f"h越界: {h}"

        # 验证元数据包含文字内容
        texts = [m.get("text", "") for m in metadata if m.get("text")]
        assert any("首页" in t for t in texts), "应包含'首页'文字"

    def test_auto_label_empty_xml(self) -> None:
        """空XML应返回空。"""
        label, metadata = DatasetManager.auto_label_from_dom("", 1080, 2400)
        assert label == ""
        assert metadata == []

    def test_auto_label_no_clickable(self) -> None:
        """纯文本无交互元素应有元数据但无YOLO标签？实际上有text的保留。"""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <hierarchy rotation="0">
          <node class="android.widget.TextView" bounds="[0,0][100,50]"
                text="纯文本" clickable="false" enabled="true"/>
        </hierarchy>"""
        label, metadata = DatasetManager.auto_label_from_dom(xml, 1080, 2400)
        # 有text内容的元素会被保留（即使不可点击）
        assert label.strip(), "有文字内容的元素应被标注"

    def test_auto_label_with_class_map(self) -> None:
        """使用预定义 class_map。"""
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <hierarchy rotation="0">
          <node class="android.widget.Button" bounds="[40,250][100,300]"
                text="登录" clickable="true" enabled="true"/>
        </hierarchy>"""
        class_map = {"Button": 0}
        label, metadata = DatasetManager.auto_label_from_dom(
            dom_xml=xml,
            image_width=1080,
            image_height=2400,
            class_map=class_map,
        )
        assert label.strip()
        parts = label.strip().split()
        assert parts[0] == "0", f"Button应该映射到ID 0, 实际: {parts[0]}"
        assert metadata[0]["text"] == "登录", "元数据应包含文字'登录'"
        assert metadata[0]["type"] == "Button"
