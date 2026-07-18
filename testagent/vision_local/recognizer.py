"""YOLOv8 + OCR 页面元素识别器。

视觉通道核心：截图 → YOLOv8 检测元素 → OCR 提取文字 → 颜色分析 → 结构化输出。

参考 mobile_vision 的 ``PageElementRecognizer``。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Callable

from testagent.vision_local.color_analyzer import ColorAnalyzer
from testagent.vision_local.types import BBox, PageStructure, VisualElement


class PageElementRecognizer:
    """视觉通道：YOLOv8 目标检测 + OCR 文字识别 + 颜色分析。

    延迟加载 YOLO 和 OCR 模型，仅在首次调用时初始化。
    """

    def __init__(
        self,
        model_path: str = "",
        confidence_threshold: float = 0.25,
        iou_threshold: float = 0.45,
        ocr_engine: str = "rapidocr",
        ocr_confidence: float = 0.1,
        device: str = "cpu",
        progress_callback: Callable[[str, float], None] | None = None,
    ) -> None:
        self._model_path = model_path
        self._conf_threshold = confidence_threshold
        self._iou_threshold = iou_threshold
        self._ocr_engine_name = ocr_engine
        self._ocr_confidence = ocr_confidence
        self._device = device
        self._progress_callback = progress_callback

        self._yolo_model: Any = None
        self._ocr_reader: Any = None
        self._class_names: dict[int, str] = {}
        self._initialized = False

        # 颜色分析器
        self._color_analyzer = ColorAnalyzer()

    def _report(self, stage: str, progress: float) -> None:
        if self._progress_callback:
            self._progress_callback(stage, progress)

    # ── 初始化 ───────────────────────────────────────────────────

    def initialize(self) -> None:
        """延迟初始化 YOLO 和 OCR 引擎。"""
        if self._initialized:
            return
        self._init_yolo()
        self._init_ocr()
        self._initialized = True

    def _init_yolo(self) -> None:
        """加载 YOLO 模型。"""
        if self._yolo_model is not None:
            return
        if not self._model_path:
            raise FileNotFoundError(
                "YOLO 模型路径未配置。请设置 yolo_model_path 或先训练模型。"
            )
        from ultralytics import YOLO

        self._yolo_model = YOLO(self._model_path)
        self._class_names = self._yolo_model.names if hasattr(self._yolo_model, "names") else {}

    def _init_ocr(self) -> None:
        """加载 OCR 引擎。"""
        if self._ocr_reader is not None:
            return

        if self._ocr_engine_name == "rapidocr":
            from rapidocr_onnxruntime import RapidOCR

            self._ocr_reader = RapidOCR(return_word_box=True)
        elif self._ocr_engine_name == "easyocr":
            import easyocr

            self._ocr_reader = easyocr.Reader(["en", "ch_sim"], gpu=False, verbose=False)
        else:
            raise ValueError(f"不支持的 OCR 引擎: {self._ocr_engine_name}，可选 'easyocr' 或 'rapidocr'")

    # ── 主识别管线 ──────────────────────────────────────────────

    def recognize_from_base64(self, image_base64: str) -> PageStructure:
        """从 base64 编码的截图识别页面元素。

        Args:
            image_base64: base64 编码的 PNG 截图数据。

        Returns:
            PageStructure 包含页面尺寸、元素列表和文本列表。
        """
        import base64

        try:
            import numpy as np
            from PIL import Image
        except ImportError:
            raise ImportError("需要安装 Pillow 和 numpy: pip install Pillow numpy")

        # 解码 base64
        image_data = base64.b64decode(image_base64)
        image = Image.open(__import__("io").BytesIO(image_data))
        image_array = np.array(image)

        return self._recognize(image_array, image.width, image.height)

    def recognize_from_image(self, image_path: str) -> PageStructure:
        """从图片文件识别页面元素。

        Args:
            image_path: 图片文件路径。

        Returns:
            PageStructure 包含页面尺寸、元素列表和文本列表。
        """
        try:
            import numpy as np
            from PIL import Image
        except ImportError:
            raise ImportError("需要安装 Pillow 和 numpy: pip install Pillow numpy")

        # 重试机制：处理截图文件损坏
        max_retry = 3
        retry_delay = 0.5
        image = None

        for attempt in range(1, max_retry + 1):
            try:
                image = Image.open(image_path)
                image.verify()
                image = Image.open(image_path)
                break
            except Exception:
                if attempt >= max_retry:
                    raise ConnectionError(f"截图文件损坏（设备可能已断连）: {image_path}")
                time.sleep(retry_delay)

        image_array = np.array(image)
        return self._recognize(image_array, image.width, image.height)

    def _recognize(self, image_array: Any, img_w: int, img_h: int) -> PageStructure:
        """核心识别管线。

        Args:
            image_array: numpy 数组表示的图像。
            img_w: 图像宽度。
            img_h: 图像高度。

        Returns:
            完整的 PageStructure。
        """
        self.initialize()
        self._report("yolo", 0.1)

        # 1. YOLOv8 检测 UI 元素
        yolo_elements = self._recognize_elements(image_array)
        self._report("yolo", 0.4)

        # 2. OCR 提取文字（在缩小版图像上运行，提高速度）
        ocr_texts = self._recognize_texts(image_array)
        self._report("ocr", 0.7)

        # 3. 整合 YOLO 元素和 OCR 文字 + 颜色分析
        visual_elements = self.integrate_elements_and_texts(
            yolo_elements, ocr_texts, image_array
        )
        self._report("integrate", 0.9)

        return PageStructure(
            page_width=img_w,
            page_height=img_h,
            source="visual",
            elements=visual_elements,
        )

    # ── YOLO 检测 ───────────────────────────────────────────────

    def _recognize_elements(self, image: Any) -> list[dict[str, Any]]:
        """YOLOv8 预测 → 提取元素。"""
        if self._yolo_model is None:
            return []

        results = self._yolo_model.predict(
            source=image,
            conf=self._conf_threshold,
            iou=self._iou_threshold,
            verbose=False,
            device=self._device,
        )

        elements: list[dict[str, Any]] = []
        for result in results:
            boxes = result.boxes
            if boxes is None:
                continue
            for idx, box in enumerate(boxes):
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                conf = float(box.conf[0].cpu().numpy())
                cls = int(box.cls[0].cpu().numpy())
                class_name = self._class_names.get(cls, f"class_{cls}")

                elements.append({
                    "id": f"elem_{idx}",
                    "type": "ui_component",
                    "class_name": class_name,
                    "bbox": {
                        "x1": int(x1),
                        "y1": int(y1),
                        "x2": int(x2),
                        "y2": int(y2),
                    },
                    "confidence": conf,
                    "source": "yolo",
                })
        return elements

    # ── OCR ─────────────────────────────────────────────────────

    def _recognize_texts(self, image: Any) -> list[dict[str, Any]]:
        """OCR 提取文字。"""
        if self._ocr_reader is None:
            return []

        # OCR 不需要全分辨率，缩小到短边 <= 1200px
        h, w = image.shape[:2]
        max_side = max(h, w)
        ocr_scale = 1.0
        ocr_array = image

        if max_side > 1200:
            import cv2

            ocr_scale = 1200.0 / max_side
            new_h, new_w = int(h * ocr_scale), int(w * ocr_scale)
            ocr_array = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_AREA)

        texts: list[dict[str, Any]] = []

        try:
            if self._ocr_engine_name == "rapidocr":
                result = self._ocr_reader(ocr_array)
                if result and result[0]:
                    for item in result[0]:
                        bbox_pts = item[0]  # [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
                        text = item[1]
                        conf = float(item[2]) if len(item) > 2 else 0.5
                        if conf < self._ocr_confidence:
                            continue
                        x_coords = [p[0] for p in bbox_pts]
                        y_coords = [p[1] for p in bbox_pts]
                        t_min_x, t_max_x = min(x_coords), max(x_coords)
                        t_min_y, t_max_y = min(y_coords), max(y_coords)
                        # 从缩放空间映射回原始图像空间
                        if ocr_scale < 1.0:
                            t_min_x /= ocr_scale
                            t_max_x /= ocr_scale
                            t_min_y /= ocr_scale
                            t_max_y /= ocr_scale
                        texts.append({
                            "id": f"text_{len(texts)}",
                            "text": text,
                            "bbox": {
                                "x1": int(t_min_x),
                                "y1": int(t_min_y),
                                "x2": int(t_max_x),
                                "y2": int(t_max_y),
                            },
                            "confidence": conf,
                            "source": "ocr",
                        })
            elif self._ocr_engine_name == "easyocr":
                result = self._ocr_reader.readtext(ocr_array)
                for item in result:
                    bbox_pts = item[0]
                    text = item[1]
                    conf = float(item[2])
                    if conf < self._ocr_confidence:
                        continue
                    x_coords = [p[0] for p in bbox_pts]
                    y_coords = [p[1] for p in bbox_pts]
                    t_min_x, t_min_y = min(x_coords), min(y_coords)
                    t_max_x, t_max_y = max(x_coords), max(y_coords)
                    if ocr_scale < 1.0:
                        t_min_x /= ocr_scale
                        t_max_x /= ocr_scale
                        t_min_y /= ocr_scale
                        t_max_y /= ocr_scale
                    texts.append({
                        "id": f"text_{len(texts)}",
                        "text": text,
                        "bbox": {
                            "x1": int(t_min_x),
                            "y1": int(t_min_y),
                            "x2": int(t_max_x),
                            "y2": int(t_max_y),
                        },
                        "confidence": conf,
                        "source": "ocr",
                    })
        except Exception:
            pass

        return texts

    # ── 元素-文字整合 + 颜色分析 ───────────────────────────────

    def integrate_elements_and_texts(
        self,
        yolo_elements: list[dict[str, Any]],
        ocr_texts: list[dict[str, Any]],
        image_array: Any,
    ) -> list[VisualElement]:
        """合并 YOLO 元素、OCR 文字和颜色分析为 ``VisualElement`` 列表。

        策略（匹配 mobile_vision 逻辑）：
        1. 对每个 YOLO 元素，检查是否有 OCR 文本 bbox 与其重叠
           （IoU > 0.3 或包含关系）。若有则关联文本。
        2. 未被关联的 OCR 文字作为独立 text_block VisualElement。
        3. 对每个元素区域运行颜色分析。
        """
        visual_elements: list[VisualElement] = []
        used_texts: set[int] = set()

        # 处理 YOLO 元素
        for yolo_el in yolo_elements:
            bbox = yolo_el["bbox"]
            yolo_box = BBox(
                x1=int(bbox["x1"]),
                y1=int(bbox["y1"]),
                x2=int(bbox["x2"]),
                y2=int(bbox["y2"]),
            )

            # 找重叠的 OCR 文字
            matched_text = ""
            for ti, t in enumerate(ocr_texts):
                if ti in used_texts:
                    continue
                t_bbox = t["bbox"]
                if _bbox_overlap_ratio(
                    yolo_box.x1, yolo_box.y1, yolo_box.x2, yolo_box.y2,
                    t_bbox["x1"], t_bbox["y1"], t_bbox["x2"], t_bbox["y2"],
                ) > 0.3:
                    matched_text = t["text"]
                    used_texts.add(ti)
                    break

            # 颜色分析
            color_info = self._color_analyzer.analyze_region(image_array, yolo_box)

            el = VisualElement(
                id=yolo_el["id"],
                type=yolo_el.get("class_name", "ui_component"),
                bbox=yolo_box,
                confidence=yolo_el["confidence"],
                text=matched_text,
                color=color_info.get("color", ""),
                color_brightness=color_info.get("brightness", 0.0),
            )
            visual_elements.append(el)

        # 未被关联的 OCR 文字作为独立 text_block
        for ti, t in enumerate(ocr_texts):
            if ti in used_texts:
                continue
            t_bbox = t["bbox"]
            bbox = BBox(
                x1=int(t_bbox["x1"]),
                y1=int(t_bbox["y1"]),
                x2=int(t_bbox["x2"]),
                y2=int(t_bbox["y2"]),
            )
            color_info = self._color_analyzer.analyze_region(image_array, bbox)

            el = VisualElement(
                id=t["id"],
                type="text_block",
                bbox=bbox,
                confidence=t["confidence"],
                text=t["text"],
                color=color_info.get("color", ""),
                color_brightness=color_info.get("brightness", 0.0),
            )
            visual_elements.append(el)

        return visual_elements

    # ── 标注绘制 ───────────────────────────────────────────────

    def draw_annotated_image(self, image: Any, elements: list[VisualElement]) -> Any:
        """在图像上绘制 YOLO 检测框和标签。

        Args:
            image: numpy 数组（BGR 或 RGB）。
            elements: 要绘制的 VisualElement 列表。

        Returns:
            绘制后的 numpy 数组。
        """
        try:
            from PIL import ImageDraw
        except ImportError:
            return image

        try:
            from PIL import Image as PILImage

            if hasattr(image, "shape"):
                img = PILImage.fromarray(image)
            else:
                img = image

            draw = ImageDraw.Draw(img)

            colors = [
                (255, 0, 0), (0, 150, 255), (0, 200, 0), (255, 0, 150),
                (0, 100, 255), (255, 200, 0), (150, 0, 255), (0, 200, 200),
            ]
            for i, el in enumerate(elements):
                color = colors[i % len(colors)]
                draw.rectangle(
                    [el.bbox.x1, el.bbox.y1, el.bbox.x2, el.bbox.y2],
                    outline=color,
                    width=2,
                )
                label = f"{el.type}: {el.confidence:.2f}"
                if el.text:
                    label += f" \"{el.text[:15]}\""
                draw.text((el.bbox.x1 + 2, el.bbox.y1 - 14), label, fill=color)

            return img
        except Exception:
            return image


# ── 工具函数 ──────────────────────────────────────────────────


def _bbox_overlap_ratio(
    ax1: int, ay1: int, ax2: int, ay2: int,
    bx1: int, by1: int, bx2: int, by2: int,
) -> float:
    """计算两个 bbox 的交并比（IoU）。"""
    x_left = max(ax1, bx1)
    y_top = max(ay1, by1)
    x_right = min(ax2, bx2)
    y_bottom = min(ay2, by2)

    if x_right <= x_left or y_bottom <= y_top:
        return 0.0

    intersection = (x_right - x_left) * (y_bottom - y_top)
    area_a = (ax2 - ax1) * (ay2 - ay1)
    area_b = (bx2 - bx1) * (by2 - by1)
    union = area_a + area_b - intersection
    if union <= 0:
        return 0.0
    return intersection / union
