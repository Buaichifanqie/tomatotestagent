"""DOM 快通道解析器。

通过 uiautomator2 获取的 Android UI hierarchy XML 解析为结构化 ``VisualElement`` 列表。

参考 mobile_vision 的 ``AndroidInterface._get_dom_elements()`` 和 ``_dom_is_rich()``。
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from testagent.vision_local.types import BBox, VisualElement


class DomParser:
    """解析 Android UI hierarchy XML 为结构化 VisualElement 列表。"""

    @staticmethod
    def parse(xml_content: str) -> tuple[list[VisualElement], int, int]:
        """解析 XML（来自 app_get_source）。

        Args:
            xml_content: uiautomator2 返回的 XML 源码。

        Returns:
            (elements, display_width, display_height)
        """
        root = ET.fromstring(xml_content.encode("utf-8"))

        # 尝试从 <hierarchy> 节点获取屏幕尺寸
        display_w = int(root.attrib.get("rotation", 0))  # not standard
        display_h = 0
        # 大部分 Android XML root 就是 <hierarchy rotation="0" …>
        # 屏幕宽高通常在节点属性中或通过 bounds 推断
        # 先用默认值，后续可通过 get_screen_size 校正
        display_w, display_h = 1080, 2400

        elements: list[VisualElement] = []
        DomParser._flatten_node(root, elements, parent_id="root", depth=0)

        return elements, display_w, display_h

    @staticmethod
    def _flatten_node(
        node: ET.Element,
        output: list[VisualElement],
        parent_id: str = "root",
        depth: int = 0,
    ) -> None:
        """递归展开 XML 节点为扁平 VisualElement 列表。"""
        if depth > 50:  # 防止过深递归
            return

        bounds_str = node.get("bounds", "")
        bbox = DomParser._parse_bounds(bounds_str)
        if bbox is None:
            bbox = BBox(x1=0, y1=0, x2=0, y2=0)

        class_name = node.get("class", "")
        type_short = class_name.split(".")[-1] if class_name else "Unknown"

        text = node.get("text", "")
        content_desc = node.get("content-desc", "")
        resource_id = node.get("resource-id", "")
        clickable = node.get("clickable", "false") == "true"
        enabled = node.get("enabled", "true") == "true"
        checkable = node.get("checkable", "false") == "true"
        checked = node.get("checked", "false") == "true"
        focusable = node.get("focusable", "false") == "true"
        focused = node.get("focused", "false") == "true"
        scrollable = node.get("scrollable", "false") == "true"
        index = node.get("index", "0")
        node_id = f"{parent_id}_{index}" if parent_id != "root" else index

        # 只保留有意义的节点（有尺寸或交互）
        bbox_w = bbox.x2 - bbox.x1
        bbox_h = bbox.y2 - bbox.y1
        has_size = bbox_w > 0 and bbox_h > 0

        if has_size:
            el = VisualElement(
                id=f"dom_{node_id}",
                type=type_short,
                bbox=bbox,
                confidence=1.0,
                text=text,
                clickable=clickable,
                enabled=enabled,
                checked=checked,
                selected=False,
                focused=focused,
                scrollable=scrollable,
                resource_id=resource_id,
                content_desc=content_desc,
                parent_id=parent_id,
            )
            output.append(el)

        # 递归子节点
        for child in node:
            DomParser._flatten_node(child, output, node_id, depth + 1)

    @staticmethod
    def _parse_bounds(bounds_str: str) -> BBox | None:
        """解析 uiautomator2 bounds 格式: ``[x1,y1][x2,y2]``"""
        if not bounds_str:
            return None
        match = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str)
        if match:
            return BBox(
                x1=int(match.group(1)),
                y1=int(match.group(2)),
                x2=int(match.group(3)),
                y2=int(match.group(4)),
            )
        return None

    # ── DOM 质量评估 ────────────────────────────────────────────

    @staticmethod
    def _is_system_ui(elem: VisualElement) -> bool:
        """判断是否为系统 UI（状态栏、导航栏）。"""
        return "com.android.systemui" in elem.resource_id or "system_icons" in (elem.resource_id or "")

    @staticmethod
    def _is_empty_container(elem: VisualElement) -> bool:
        """判断是否为空布局容器。"""
        if elem.text or elem.content_desc:
            return False
        return elem.type in ("View", "FrameLayout", "LinearLayout", "RelativeLayout")

    @staticmethod
    def is_rich_dom(elements: list[VisualElement]) -> bool:
        """判断 DOM 是否足够丰富，值得使用 DOM 通道。

        规则（匹配 mobile_vision 逻辑）：
        - 排除系统 UI 元素
        - 排除空布局容器
        - 剩余元素中至少 3 个 clickable，至少 1 个有文本
        """
        clickable_count = 0
        text_count = 0
        webview_count = 0
        filtered_out = 0

        for elem in elements:
            if DomParser._is_system_ui(elem):
                filtered_out += 1
                continue
            if DomParser._is_empty_container(elem):
                filtered_out += 1
                continue

            if "WebView" in elem.type:
                webview_count += 1

            if elem.clickable:
                clickable_count += 1

            if elem.text or elem.content_desc:
                text_count += 1

        remaining = len(elements) - filtered_out
        if webview_count > 0 and remaining > 0 and webview_count > remaining * 0.3:
            return False

        rich = clickable_count >= 3 and text_count >= 1
        return rich

    @staticmethod
    def to_visual_elements(elements: list[dict[str, Any]]) -> list[VisualElement]:
        """将字典列表转换为 VisualElement 列表（兼容外部数据）。"""
        result: list[VisualElement] = []
        for e in elements:
            if isinstance(e, VisualElement):
                result.append(e)
            elif isinstance(e, dict):
                bbox_data = e.get("bbox", {})
                if isinstance(bbox_data, (list, tuple)) and len(bbox_data) == 4:
                    bbox = BBox(x1=bbox_data[0], y1=bbox_data[1], x2=bbox_data[2], y2=bbox_data[3])
                elif isinstance(bbox_data, dict):
                    bbox = BBox(**{k: int(v) for k, v in bbox_data.items()})
                else:
                    bbox = BBox(x1=0, y1=0, x2=0, y2=0)
                el = VisualElement(
                    id=e.get("id", ""),
                    type=e.get("type", e.get("type_short", "unknown")),
                    bbox=bbox,
                    confidence=float(e.get("confidence", 1.0)),
                    text=e.get("text", ""),
                    clickable=e.get("clickable", False),
                    enabled=e.get("enabled", True),
                    checked=e.get("checked", False),
                    resource_id=e.get("resource_id", ""),
                    content_desc=e.get("content_desc", ""),
                )
                result.append(el)
        return result
