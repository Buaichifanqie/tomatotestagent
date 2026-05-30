"""UI 树清洗与页面哈希计算工具.

提供从 Appium XML page source 中提取稳定特征、计算页面哈希的功能，
用于坐标缓存的键生成。
"""
from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET

# 需要保留的语义/结构属性
_KEEP_ATTRS = frozenset({"class", "resource-id", "clickable", "enabled"})

# 需要清洗的文本属性（数字会被替换为占位符）
_TEXT_ATTRS = frozenset({"text", "content-desc", "label"})

# 数字替换正则
_NUM_PATTERN = re.compile(r"\d+")


def clean_ui_tree(node: ET.Element) -> dict:
    """清洗 UI 树节点，保留稳定特征."""
    stable_attrs: dict[str, str] = {}
    for attr in _KEEP_ATTRS:
        value = node.attrib.get(attr)
        if value is not None:
            stable_attrs[attr] = value
    for attr in _TEXT_ATTRS:
        value = node.attrib.get(attr)
        if value is not None:
            cleaned = _NUM_PATTERN.sub("N", value)
            if cleaned:
                stable_attrs[attr] = cleaned
    children = [clean_ui_tree(child) for child in node]
    return {"attrs": stable_attrs, "children": children}


def compute_page_hash(cleaned_tree: dict) -> str:
    """计算清洗后 UI 树的哈希."""
    serialized = json.dumps(cleaned_tree, sort_keys=True, ensure_ascii=False)
    return hashlib.md5(serialized.encode()).hexdigest()[:12]


def get_page_hash_from_source(xml_source: str) -> str:
    """从 XML page source 字符串计算页面哈希."""
    try:
        root = ET.fromstring(xml_source)
        cleaned = clean_ui_tree(root)
        return compute_page_hash(cleaned)
    except ET.ParseError:
        return ""
