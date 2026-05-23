from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock

from testagent.plan.session_manager import SessionManager, SessionState


class TestSessionState:
    """SessionState initial state and state transitions."""

    def test_initial_state(self) -> None:
        state = SessionState()
        assert state.connected is False
        assert state.recovery_count == 0
        assert state.created_at == ""
        assert state.device_info == {}

    def test_mark_connected(self) -> None:
        state = SessionState()
        state.mark_connected()
        assert state.connected is True

    def test_mark_disconnected(self) -> None:
        state = SessionState()
        state.mark_connected()
        state.mark_disconnected()
        assert state.connected is False

    def test_record_recovery(self) -> None:
        state = SessionState()
        state.record_recovery()
        assert state.recovery_count == 1
        state.record_recovery()
        assert state.recovery_count == 2


class TestSessionManagerInit:
    """SessionManager constructor."""

    def test_default_retry_limit(self) -> None:
        mgr = SessionManager()
        assert mgr.retry_limit == 2
        assert mgr.session_state is not None

    def test_custom_retry_limit(self) -> None:
        mgr = SessionManager(retry_limit=5)
        assert mgr.retry_limit == 5


class TestSessionProperty:
    """The session property."""

    def test_session_returns_driver(self) -> None:
        mgr = SessionManager()
        assert mgr.session is None
        mock_driver = MagicMock()
        mgr._driver = mock_driver
        assert mgr.session is mock_driver


class TestIsConnected:
    """is_connected method."""

    def test_returns_false_when_no_driver(self) -> None:
        mgr = SessionManager()
        assert mgr.is_connected() is False

    def test_returns_true_when_driver_responds(self) -> None:
        mgr = SessionManager()
        mock_driver = MagicMock()
        mgr._driver = mock_driver
        assert mgr.is_connected() is True

    def test_returns_false_when_driver_throws(self) -> None:
        mgr = SessionManager()
        mock_driver = MagicMock()
        type(mock_driver).current_activity = PropertyMock(
            side_effect=Exception("connection lost")
        )
        mgr._driver = mock_driver
        assert mgr.is_connected() is False


class TestNeedsRecovery:
    """needs_recovery method."""

    def test_returns_true_when_not_connected(self) -> None:
        mgr = SessionManager()
        assert mgr.needs_recovery() is True

    def test_returns_false_when_connected(self) -> None:
        mgr = SessionManager()
        mock_driver = MagicMock()
        mgr._driver = mock_driver
        assert mgr.needs_recovery() is False


class TestShouldAbort:
    """should_abort method."""

    def test_returns_true_when_exceeded_limit(self) -> None:
        mgr = SessionManager(retry_limit=2)
        mgr.session_state.recovery_count = 2
        assert mgr.should_abort() is True

    def test_returns_false_within_limit(self) -> None:
        mgr = SessionManager(retry_limit=3)
        mgr.session_state.recovery_count = 2
        assert mgr.should_abort() is False
