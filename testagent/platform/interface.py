from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseRecorder(ABC):
    """Platform-abstracted screen recorder."""

    def __init__(self, output_dir: str, tc_id: str, device_udid: str) -> None:
        self._output_dir = output_dir
        self._tc_id = tc_id
        self._device_udid = device_udid

    @abstractmethod
    async def start(self) -> bool: ...

    @abstractmethod
    async def stop(self) -> None: ...

    @abstractmethod
    async def check_and_split(self) -> None: ...

    @abstractmethod
    def get_segments(self) -> list[str]: ...


class SessionInfo:
    """Bucket for session references needed by platform operations."""
    def __init__(self, appium_url: str = "", session_id: str = "", device_udid: str = "") -> None:
        self.appium_url = appium_url
        self.session_id = session_id
        self.device_udid = device_udid


class AbstractPlatform(ABC):
    """Strategy interface for mobile-platform-specific behavior."""

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Return "Android" or "iOS"."""

    @property
    @abstractmethod
    def automation_name(self) -> str:
        """Return "UiAutomator2" or "XCUITest"."""

    @abstractmethod
    def build_capabilities(self, udid: str = "", **kwargs: Any) -> dict[str, Any]:
        """Build Appium desired capabilities dict for this platform."""

    @abstractmethod
    async def launch_app(self, app_id: str, session_info: SessionInfo) -> dict[str, Any]:
        """Launch the app by identifier (packageName / bundleId)."""

    @abstractmethod
    async def teardown_app(self, app_id: str, session_info: SessionInfo) -> None:
        """Clean up app state between test cases."""

    @abstractmethod
    async def list_connected_devices(self) -> list[dict[str, str]]:
        """Return list of connected devices [{udid, name, status}, ...]."""

    @abstractmethod
    async def detect_installed_apps(self, udid: str) -> list[str]:
        """Return list of installed 3rd-party app identifiers on device."""

    @abstractmethod
    async def detect_app_version(self, app_id: str, udid: str) -> str | None:
        """Return version string of the installed app, or None."""

    @abstractmethod
    async def go_back(self, session_info: SessionInfo) -> bool:
        """Press the platform's back/return navigation."""

    @abstractmethod
    async def press_keyboard_done(self, session_info: SessionInfo) -> bool:
        """Press the keyboard's done/search/enter key."""

    @abstractmethod
    async def get_screen_size(self, session_info: SessionInfo) -> tuple[int, int]:
        """Return (width, height) in pixels."""

    @abstractmethod
    def get_find_element_strategies(self) -> list[str]:
        """Return list of valid element-finding strategies for this platform."""

    @abstractmethod
    def get_default_selector_strategy(self) -> str:
        """Return the default strategy string for Appium _find_element."""

    @abstractmethod
    def create_recorder(
        self,
        output_dir: str,
        tc_id: str,
        device_udid: str = "",
        session_manager: Any = None,
    ) -> BaseRecorder:
        """Create a platform-appropriate screen recorder instance."""

    @abstractmethod
    def get_appium_args(self) -> list[str]:
        """Return extra CLI args for starting the Appium server."""
