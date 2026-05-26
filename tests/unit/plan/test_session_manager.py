from __future__ import annotations

from unittest.mock import patch

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
        assert mgr.appium_url == "http://localhost:4723"

    def test_custom_retry_limit(self) -> None:
        mgr = SessionManager(retry_limit=5)
        assert mgr.retry_limit == 5

    def test_custom_appium_url(self) -> None:
        mgr = SessionManager(appium_url="http://custom:4723")
        assert mgr.appium_url == "http://custom:4723"

    def test_initial_session_id_is_none(self) -> None:
        mgr = SessionManager()
        assert mgr.session_id is None
        assert mgr.session is None


class TestSessionProperty:
    """The session property (alias for session_id)."""

    def test_session_returns_none_by_default(self) -> None:
        mgr = SessionManager()
        assert mgr.session is None

    def test_session_returns_session_id(self) -> None:
        mgr = SessionManager()
        mgr._session_id = "test-sid-123"
        assert mgr.session == "test-sid-123"
        assert mgr.session_id == "test-sid-123"


class TestCreateSession:
    """create_session method."""

    @patch("httpx.post")
    def test_create_session_success(self, mock_post) -> None:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"value": {"sessionId": "test-sid-123"}}
        mgr = SessionManager()
        sid = mgr.create_session()
        assert sid == "test-sid-123"
        assert mgr.session_id == "test-sid-123"
        assert mgr.session_state.connected is True

    @patch("httpx.post")
    def test_create_session_failure_status(self, mock_post) -> None:
        mock_post.return_value.status_code = 500
        mgr = SessionManager()
        sid = mgr.create_session()
        assert sid is None
        assert mgr.session_id is None
        assert mgr.session_state.connected is False

    @patch("httpx.post")
    def test_create_session_no_session_id_in_response(self, mock_post) -> None:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"value": {}}
        mgr = SessionManager()
        sid = mgr.create_session()
        assert sid is None
        assert mgr.session_id is None

    @patch("httpx.post")
    def test_create_session_network_error(self, mock_post) -> None:
        mock_post.side_effect = Exception("Connection refused")
        mgr = SessionManager()
        sid = mgr.create_session()
        assert sid is None
        assert mgr.session_id is None

    @patch("httpx.post")
    def test_create_session_uses_correct_url(self, mock_post) -> None:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"value": {"sessionId": "sid"}}
        mgr = SessionManager(appium_url="http://custom:4723")
        mgr.create_session()
        mock_post.assert_called_once()
        args, _ = mock_post.call_args
        assert args[0] == "http://custom:4723/session"

    @patch("httpx.post")
    def test_create_session_sends_capabilities(self, mock_post) -> None:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"value": {"sessionId": "sid"}}
        mgr = SessionManager()
        mgr.create_session()
        _, kwargs = mock_post.call_args
        caps = kwargs["json"]
        assert "capabilities" in caps
        always_match = caps["capabilities"]["alwaysMatch"]
        assert always_match["platformName"] == "Android"
        assert always_match["appium:automationName"] == "UiAutomator2"


class TestIsConnected:
    """is_connected method."""

    def test_returns_false_when_no_session_id(self) -> None:
        mgr = SessionManager()
        assert mgr.is_connected() is False

    @patch("httpx.get")
    def test_returns_true_when_session_alive(self, mock_get) -> None:
        mock_get.return_value.status_code = 200
        mgr = SessionManager()
        mgr._session_id = "test-sid"
        assert mgr.is_connected() is True

    @patch("httpx.get")
    def test_returns_false_when_session_dead(self, mock_get) -> None:
        mock_get.return_value.status_code = 404
        mgr = SessionManager()
        mgr._session_id = "test-sid"
        assert mgr.is_connected() is False

    @patch("httpx.get")
    def test_returns_false_on_network_error(self, mock_get) -> None:
        mock_get.side_effect = Exception("Connection lost")
        mgr = SessionManager()
        mgr._session_id = "test-sid"
        assert mgr.is_connected() is False

    @patch("httpx.get")
    def test_checks_correct_session_url(self, mock_get) -> None:
        mock_get.return_value.status_code = 200
        mgr = SessionManager(appium_url="http://custom:4723")
        mgr._session_id = "my-sid"
        mgr.is_connected()
        mock_get.assert_called_once_with("http://custom:4723/session/my-sid", timeout=10)


class TestNeedsRecovery:
    """needs_recovery method."""

    def test_returns_true_when_not_connected(self) -> None:
        mgr = SessionManager()
        assert mgr.needs_recovery() is True

    @patch("httpx.get")
    def test_returns_false_when_connected(self, mock_get) -> None:
        mock_get.return_value.status_code = 200
        mgr = SessionManager()
        mgr._session_id = "test-sid"
        assert mgr.needs_recovery() is False


class TestRecoverSession:
    """recover_session method."""

    @patch("httpx.post")
    def test_recover_session_creates_new_session(self, mock_post) -> None:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"value": {"sessionId": "new-sid-456"}}
        mgr = SessionManager()
        mgr._session_id = "old-sid"
        mgr.session_state.connected = True
        sid = mgr.recover_session()
        assert sid == "new-sid-456"
        assert mgr.session_id == "new-sid-456"
        assert mgr.session_state.connected is True
        assert mgr.session_state.recovery_count == 0  # reset on success

    @patch("httpx.post")
    def test_recover_session_failure(self, mock_post) -> None:
        mock_post.return_value.status_code = 500
        mgr = SessionManager()
        mgr._session_id = "old-sid"
        sid = mgr.recover_session()
        assert sid is None
        assert mgr.session_id is None
        assert mgr.session_state.connected is False
        assert mgr.session_state.recovery_count == 1


class TestResetRecovery:
    """reset_recovery method."""

    def test_resets_recovery_count_to_zero(self) -> None:
        mgr = SessionManager()
        mgr.session_state.recovery_count = 5
        mgr.reset_recovery()
        assert mgr.session_state.recovery_count == 0
        assert mgr.should_abort() is False


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

    def test_returns_false_when_zero_recoveries(self) -> None:
        mgr = SessionManager()
        assert mgr.should_abort() is False
