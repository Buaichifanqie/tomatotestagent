"""元素识别策略工厂（ElementSourceFactory）。

遵循 ``PlatformFactory`` 注册表模式。
"""
from __future__ import annotations

from typing import Any

from testagent.vision_local.strategy import (
    ElementSourceStrategy,
    LocalVisionStrategy,
    MultimodalVisionStrategy,
)


class ElementSourceFactory:
    """元素识别策略工厂。

    用法::

        strategy = ElementSourceFactory.create("yolo", vision_engine=engine)
        result = await strategy.find_element("搜索框", session_manager)
    """

    _registry: dict[str, type[ElementSourceStrategy]] = {}

    @classmethod
    def register(cls, name: str, strategy_cls: type[ElementSourceStrategy]) -> None:
        """注册一个新的策略实现。"""
        cls._registry[name.lower()] = strategy_cls

    @classmethod
    def create(cls, name: str, **kwargs: Any) -> ElementSourceStrategy:
        """按名称创建策略实例。

        Args:
            name: 策略名称 ("multimodal", "yolo", "yolo_with_dom")。
            **kwargs: 传递给策略构造函数的参数。

        Returns:
            ElementSourceStrategy 实例。

        Raises:
            ValueError: 未知策略名称。
        """
        name = name.lower()

        # 懒注册内置策略
        if not cls._registry:
            cls._register_builtins()

        strategy_cls = cls._registry.get(name)
        if strategy_cls is None:
            raise ValueError(
                f"未知的元素识别策略: {name}。"
                f"可选: {', '.join(cls._registry.keys())}"
            )

        # "yolo" 策略默认关闭 DOM（纯视觉），"yolo_with_dom" 开启 DOM
        if name == "yolo" and "use_dom" not in kwargs:
            kwargs["use_dom"] = False

        return strategy_cls(**kwargs)

    @classmethod
    def _register_builtins(cls) -> None:
        """注册内置策略。"""
        cls._registry["multimodal"] = MultimodalVisionStrategy
        cls._registry["yolo"] = LocalVisionStrategy
        cls._registry["yolo_with_dom"] = LocalVisionStrategy

    @classmethod
    def list_strategies(cls) -> list[str]:
        """列出所有已注册的策略名称。"""
        if not cls._registry:
            cls._register_builtins()
        return list(cls._registry.keys())
