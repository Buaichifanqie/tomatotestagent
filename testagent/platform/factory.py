# testagent/platform/factory.py
from __future__ import annotations

from testagent.platform.interface import AbstractPlatform


class PlatformFactory:
    """Creates platform strategy instances by name."""

    _registry: dict[str, type[AbstractPlatform]] = {}

    @classmethod
    def register(cls, name: str, platform_cls: type[AbstractPlatform]) -> None:
        cls._registry[name.lower()] = platform_cls

    @classmethod
    def create(cls, name: str) -> AbstractPlatform:
        name = name.lower()
        if name not in cls._registry:
            from testagent.platform.android_platform import AndroidPlatform
            from testagent.platform.ios_platform import iOSPlatform
            cls._registry["android"] = AndroidPlatform
            cls._registry["ios"] = iOSPlatform
        if name not in cls._registry:
            raise ValueError(f"Unknown platform: {name}. Must be 'android' or 'ios'.")
        return cls._registry[name]()

    @classmethod
    def list_platforms(cls) -> list[str]:
        return list(cls._registry.keys())
