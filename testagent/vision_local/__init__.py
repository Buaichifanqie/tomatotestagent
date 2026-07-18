"""本地视觉解析模块（基于 YOLOv8 + OCR + 结构化页面解析）。

提供与现有多模态大模型方案并行的本地元素识别能力。
用户可通过 --element-source 参数在 multimodal / yolo / yolo_with_dom 间切换。
"""
from __future__ import annotations

__all__ = [
    "ElementSource",
    "BBox",
    "VisualElement",
    "PageStructure",
    "DomParser",
    "ColorAnalyzer",
    "PageElementRecognizer",
    "LocalVisionEngine",
    "ElementSourceStrategy",
    "MultimodalVisionStrategy",
    "LocalVisionStrategy",
    "ElementSourceFactory",
    "DatasetManager",
    "ModelManager",
    "YOLOTrainer",
]
