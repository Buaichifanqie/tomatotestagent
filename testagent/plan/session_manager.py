from __future__ import annotations

from testagent.common.appium_manager import ensure_android_home


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
        self._device_udid: str = ""
        self._system_port: int = 8200

    @property
    def session_id(self) -> str | None:
        """Return the current session ID."""
        return self._session_id

    @property
    def session(self) -> str | None:
        """Return the current session ID (alias for backward compatibility)."""
        return self._session_id

    def create_session(self, device_udid: str = "", system_port: int = 8200) -> str | None:
        """Create an Appium session via HTTP POST.

        Args:
            device_udid: Target device serial (overrides default ``emulator-5554``).
            system_port: UiAutomator2 systemPort for this device.

        Returns:
            The session ID string, or None if creation failed.
        """
        # Store for later recovery
        if device_udid:
            self._device_udid = device_udid
        if system_port != 8200:
            self._system_port = system_port

        android_home = ensure_android_home()
        udid = device_udid or self._device_udid or "emulator-5554"
        caps: dict[str, object] = {
            "platformName": "Android",
            "appium:automationName": "UiAutomator2",
            "appium:deviceName": udid,
            "appium:udid": udid,
            "appium:noReset": True,
            "appium:autoGrantPermissions": True,
            "appium:newCommandTimeout": 300,
            "appium:allowInsecure": "*:adb_shell",
            "appium:systemPort": system_port,
        }
        if android_home:
            caps["appium:androidHome"] = android_home
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

    def close_session(self) -> None:
        """Close the current Appium session via HTTP DELETE.

        Sends a DELETE request to terminate the session and resets internal
        state. Safe to call when no session exists (no-op).
        """
        if not self._session_id:
            return
        import httpx

        try:
            with httpx.Client(timeout=10) as client:
                client.delete(f"{self.appium_url}/session/{self._session_id}")
        except Exception:
            pass
        finally:
            self._session_id = None
            self.session_state.mark_disconnected()

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
        session. Resets the recovery counter on success so that a
        successfully recovered session does not accumulate toward the
        retry limit.

        Returns:
            The new session ID string, or None if recovery failed.
        """
        self._session_id = None
        self.session_state.mark_disconnected()
        self.session_state.record_recovery()
        sid = self.create_session(
            device_udid=self._device_udid,
            system_port=self._system_port,
        )
        if sid:
            # Successful recovery — reset the counter so a stable
            # session doesn't exhaust the retry limit.
            self.session_state.recovery_count = 0
        return sid

    def reset_recovery(self) -> None:
        """Reset the recovery counter so should_abort returns False.

        Call this when creating a brand-new session (not recovering an
        existing one) to avoid exhausting the retry limit on a session
        that died through no fault of the recovery mechanism.
        """
        self.session_state.recovery_count = 0

    def should_abort(self) -> bool:
        """Return True when recovery attempts have reached the retry limit."""
        return self.session_state.recovery_count >= self.retry_limit
