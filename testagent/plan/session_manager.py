from __future__ import annotations

from typing import Any


class SessionState:
    """Tracks the state of an Appium session."""

    def __init__(self) -> None:
        self.connected: bool = False
        self.recovery_count: int = 0
        self.created_at: str = ""
        self.device_info: dict = {}

    def mark_connected(self) -> None:
        """Mark the session as connected."""
        self.connected = True

    def mark_disconnected(self) -> None:
        """Mark the session as disconnected."""
        self.connected = False

    def record_recovery(self) -> None:
        """Increment the recovery attempt counter."""
        self.recovery_count += 1


class SessionManager:
    """Manages an Appium session lifecycle with health checks and recovery."""

    def __init__(self, retry_limit: int = 2) -> None:
        self.retry_limit = retry_limit
        self.session_state = SessionState()
        self._driver: Any = None

    @property
    def session(self) -> Any:
        """Return the current driver instance."""
        return self._driver

    def is_connected(self) -> bool:
        """Check whether the driver session is alive.

        Returns False when no driver is set, or when the driver raises
        an exception on access (e.g. lost connection).
        """
        if self._driver is None:
            return False
        return self._check_driver(self._driver)

    def needs_recovery(self) -> bool:
        """Return True when the session is not connected."""
        return not self.is_connected()

    def should_abort(self) -> bool:
        """Return True when recovery attempts have reached the retry limit."""
        return self.session_state.recovery_count >= self.retry_limit

    @staticmethod
    def _check_driver(driver: Any) -> bool:
        """Probe the driver by accessing current_activity.

        Returns True if the access succeeds, False on any exception.
        """
        try:
            _ = driver.current_activity
            return True
        except Exception:
            return False
