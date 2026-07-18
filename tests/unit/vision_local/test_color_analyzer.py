"""Tests for vision_local color analyzer."""
from __future__ import annotations

import numpy as np

from testagent.vision_local.color_analyzer import ColorAnalyzer
from testagent.vision_local.types import BBox


class TestColorAnalyzer:
    def test_classify_white(self) -> None:
        assert ColorAnalyzer._classify_color(255, 255, 255) == "white"
        assert ColorAnalyzer._classify_color(230, 230, 230) == "white"  # >= 210 → white

    def test_classify_black(self) -> None:
        assert ColorAnalyzer._classify_color(0, 0, 0) == "black"
        assert ColorAnalyzer._classify_color(20, 20, 20) == "black"

    def test_classify_red(self) -> None:
        assert ColorAnalyzer._classify_color(200, 50, 50) == "red"

    def test_classify_green(self) -> None:
        assert ColorAnalyzer._classify_color(50, 200, 50) == "green"

    def test_classify_blue(self) -> None:
        assert ColorAnalyzer._classify_color(50, 50, 200) == "blue"

    def test_classify_yellow(self) -> None:
        assert ColorAnalyzer._classify_color(220, 220, 50) == "yellow"

    def test_classify_orange(self) -> None:
        assert ColorAnalyzer._classify_color(230, 150, 40) == "orange"

    def test_calculate_brightness(self) -> None:
        b = ColorAnalyzer._calculate_brightness(255, 255, 255)
        assert b == 255.0
        b = ColorAnalyzer._calculate_brightness(0, 0, 0)
        assert b == 0.0
        b = ColorAnalyzer._calculate_brightness(128, 128, 128)
        assert abs(b - 128.0) < 1.0

    def test_analyze_region_white(self) -> None:
        image = np.ones((100, 100, 3), dtype=np.uint8) * 255
        bbox = BBox(x1=10, y1=10, x2=50, y2=50)
        result = ColorAnalyzer.analyze_region(image, bbox)
        assert result["color"] == "white"
        assert result["brightness"] > 250

    def test_analyze_region_black(self) -> None:
        image = np.zeros((100, 100, 3), dtype=np.uint8)
        bbox = BBox(x1=10, y1=10, x2=50, y2=50)
        result = ColorAnalyzer.analyze_region(image, bbox)
        assert result["color"] in ("black", "unknown")

    def test_analyze_region_invalid_bbox(self) -> None:
        image = np.ones((100, 100, 3), dtype=np.uint8)
        bbox = BBox(x1=-10, y1=-10, x2=5, y2=5)  # negative coords clamped
        result = ColorAnalyzer.analyze_region(image, bbox)
        assert "color" in result

    def test_analyze_region_empty(self) -> None:
        image = np.ones((100, 100, 3), dtype=np.uint8)
        # bbox of zero size
        bbox = BBox(x1=10, y1=10, x2=10, y2=10)
        result = ColorAnalyzer.analyze_region(image, bbox)
        assert result["color"] in ("unknown", "white")
