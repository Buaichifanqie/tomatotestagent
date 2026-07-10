from testagent.platform.interface import AbstractPlatform, BaseRecorder, SessionInfo
from testagent.platform.factory import PlatformFactory

# Concrete implementations are loaded lazily via PlatformFactory.
# Direct imports (AndroidPlatform, iOSPlatform, etc.) are added by
# each platform module's own task.

__all__ = [
    "AbstractPlatform",
    "BaseRecorder",
    "SessionInfo",
    "PlatformFactory",
]
