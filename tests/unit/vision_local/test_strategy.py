"""Tests for vision_local strategy and factory."""
from __future__ import annotations

import pytest

from testagent.vision_local.factory import ElementSourceFactory
from testagent.vision_local.strategy import (
    LocalVisionStrategy,
    MultimodalVisionStrategy,
)


class TestElementSourceFactory:
    def test_create_multimodal(self) -> None:
        strategy = ElementSourceFactory.create("multimodal")
        assert isinstance(strategy, MultimodalVisionStrategy)
        assert strategy.name == "multimodal"

    def test_create_yolo(self) -> None:
        strategy = ElementSourceFactory.create("yolo")
        assert isinstance(strategy, LocalVisionStrategy)
        assert strategy.name == "yolo"

    def test_create_yolo_with_dom(self) -> None:
        strategy = ElementSourceFactory.create("yolo_with_dom")
        assert isinstance(strategy, LocalVisionStrategy)

    def test_create_unknown(self) -> None:
        with pytest.raises(ValueError, match="未知的元素识别策略"):
            ElementSourceFactory.create("unknown")

    def test_list_strategies(self) -> None:
        strategies = ElementSourceFactory.list_strategies()
        assert "multimodal" in strategies
        assert "yolo" in strategies
        assert "yolo_with_dom" in strategies
