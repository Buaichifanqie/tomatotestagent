"""大津法（Otsu's threshold）颜色分析模块。

用于分析 UI 元素区域的颜色和亮度，判断元素的交互状态
（选中/未选中/禁用等），参考 mobile_vision 的颜色分析逻辑。
"""
from __future__ import annotations

from typing import Any

from testagent.vision_local.types import BBox


class ColorAnalyzer:
    """通过 Otsu 阈值法分析元素区域颜色和亮度。"""

    @staticmethod
    def analyze_region(
        image: Any,
        bbox: BBox,
    ) -> dict[str, Any]:
        """分析图像中指定 bbox 区域的主色和亮度。

        使用 Otsu 阈值分离前景/背景，然后计算主色。

        Args:
            image: 图像数据 (numpy.ndarray, HWC/BGR)。
            bbox: 要分析的像素区域。

        Returns:
            {"color": "dark_gray"|"white"|"red"|..., "brightness": 0-255}
        """
        try:
            import numpy as np
        except ImportError:
            return {"color": "unknown", "brightness": 0.0}

        h, w = image.shape[:2]
        x1 = max(0, min(bbox.x1, w - 1))
        y1 = max(0, min(bbox.y1, h - 1))
        x2 = max(x1 + 1, min(bbox.x2, w))
        y2 = max(y1 + 1, min(bbox.y2, h))

        if x2 - x1 < 2 or y2 - y1 < 2:
            return {"color": "unknown", "brightness": 0.0}

        region = image[y1:y2, x1:x2]

        # 转灰
        gray = np.mean(region, axis=2).astype(np.uint8) if region.ndim == 3 else region

        # Otsu 阈值
        try:
            _, mask = cv2_threshold(gray)
        except Exception:
            # fallback: 用均值做简单二值化
            mask = gray > np.mean(gray)

        # 根据 mask 提取前景像素
        if np.any(mask):
            fg = region[mask]
        else:
            fg = region.reshape(-1, region.shape[2]) if region.ndim == 3 else region.ravel()

        if fg.size == 0:
            return {"color": "unknown", "brightness": 0.0}

        # 计算前景平均 RGB
        if region.ndim == 3:
            avg_r = float(np.mean(fg[:, 2]))  # BGR -> R
            avg_g = float(np.mean(fg[:, 1]))
            avg_b = float(np.mean(fg[:, 0]))
        else:
            avg_r = avg_g = avg_b = float(np.mean(fg))

        color_name = ColorAnalyzer._classify_color(avg_r, avg_g, avg_b)
        brightness = ColorAnalyzer._calculate_brightness(avg_r, avg_g, avg_b)

        return {"color": color_name, "brightness": round(brightness, 1)}

    @staticmethod
    def _classify_color(r: float, g: float, b: float) -> str:
        """将 RGB 值分类为人类可读的颜色名称。

        参考 mobile_vision 的颜色分类逻辑。
        """
        # 灰度检测
        if abs(r - g) < 20 and abs(g - b) < 20:
            if r < 40:
                return "black"
            elif r < 90:
                return "dark_gray"
            elif r < 160:
                return "gray"
            elif r < 210:
                return "light_gray"
            else:
                return "white"

        # 彩色检测：取最大值通道
        max_val = max(r, g, b)
        min_val = min(r, g, b)
        saturation = (max_val - min_val) / max_val if max_val > 0 else 0
        if saturation < 0.15:
            # 低饱和度，转为灰度
            brightness = (r + g + b) / 3
            if brightness < 40:
                return "black"
            elif brightness < 90:
                return "dark_gray"
            elif brightness < 160:
                return "gray"
            elif brightness < 210:
                return "light_gray"
            else:
                return "white"

        if r > 180 and g < 120 and b < 120:
            return "red"
        if r < 100 and g > 160 and b < 100:
            return "green"
        if r < 100 and g < 100 and b > 180:
            return "blue"
        if r > 200 and g > 200 and b < 100:
            return "yellow"
        if r > 220 and g > 140 and b < 80:
            return "orange"
        if r > 140 and g < 80 and b > 140:
            return "purple"
        if r < 80 and g > 140 and b > 140:
            return "cyan"

        return "unknown"

    @staticmethod
    def _calculate_brightness(r: float, g: float, b: float) -> float:
        """用亮度权重计算感知亮度 (0-255)。"""
        return 0.299 * r + 0.587 * g + 0.114 * b


def cv2_threshold(gray: Any) -> tuple[float, Any]:
    """封装 OpenCV 的 Otsu 阈值调用，便于测试时 mock。"""
    import cv2

    return cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
