from __future__ import annotations


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
    """Manages an Appium session lifecycle with health checks and recovery.

    Creates Appium sessions via HTTP POST to the Appium server and checks
    session health via HTTP GET. Recovery is attempted when the session
    is lost, up to a configurable retry limit.
    """

    def __init__(self, retry_limit: int = 2, appium_url: str = "http://localhost:4723") -> None:
        self.retry_limit = retry_limit
        self.appium_url = appium_url
        self._session_id: str | None = None
        self.session_state = SessionState()

    @property
    def session_id(self) -> str | None:
        """Return the current session ID."""
        return self._session_id

    @property
    def session(self) -> str | None:
        """Return the current session ID (alias for backward compatibility)."""
        return self._session_id

    def create_session(self) -> str | None:
        """Create an Appium session via HTTP POST.

        Sends Android capabilities to the Appium server and returns the
        session ID string on success, or None on failure.

        Returns:
            The session ID string, or None if creation failed.
        """
        caps = {
            "platformName": "Android",
            "appium:automationName": "UiAutomator2",
            "appium:deviceName": "emulator-5554",
            "appium:noReset": True,
            "appium:autoGrantPermissions": True,
            "appium:newCommandTimeout": 120,
        }
        capabilities = {"capabilities": {"alwaysMatch": caps, "firstMatch": [{}]}}
        try:
            import httpx

            resp = httpx.post(
                f"{self.appium_url}/session",
                json=capabilities,
                timeout=30,
            )
            if resp.status_code == 200:
                data = resp.json()
                sid = data.get("value", {}).get("sessionId") or data.get("sessionId")
                if sid:
                    self._session_id = sid
                    self.session_state.mark_connected()
                    return sid
        except Exception:
            pass
        return None

    def is_connected(self) -> bool:
        """Check whether the session is alive via HTTP health check.

        Sends a GET request to the session endpoint. Returns True when
        the server responds with 200, False otherwise.

        Returns:
            True if the session is alive, False otherwise.
        """
        if not self._session_id:
            return False
        try:
            import httpx

            resp = httpx.get(
                f"{self.appium_url}/session/{self._session_id}",
                timeout=10,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def needs_recovery(self) -> bool:
        """Return True when the session is not connected."""
        return not self.is_connected()

    def recover_session(self) -> str | None:
        """Attempt to recover the session by creating a new one.

        Clears the current session ID, marks the state as disconnected,
        increments the recovery counter, and attempts to create a fresh
        session.

        Returns:
            The new session ID string, or None if recovery failed.
        """
        self._session_id = None
        self.session_state.mark_disconnected()
        self.session_state.record_recovery()
        return self.create_session()

    def should_abort(self) -> bool:
        """Return True when recovery attempts have reached the retry limit."""
        return self.session_state.recovery_count >= self.retry_limit
