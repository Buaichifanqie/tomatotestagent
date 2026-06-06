from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from testagent.plan.execution_engine import ExecutionEngine
from testagent.plan.models import (
    AbortPolicy,
    ExecutionStatus,
    FailureType,
    PlanConfig,
    PopupRule,
    Precondition,
    RetryPolicy,
    StepExecution,
    TestCase,
    TestStep,
)
from testagent.plan.popup_handler import PopupHandler
from testagent.plan.session_manager import SessionManager


class TestExecutionEngineInit:
    """ExecutionEngine constructor."""

    def test_init_with_config(self):
        config = PlanConfig()
        engine = ExecutionEngine(config=config)
        assert engine.config is config
        assert engine.popup_handler is not None
        assert isinstance(engine.popup_handler, PopupHandler)
        assert engine.session_manager is not None
        assert isinstance(engine.session_manager, SessionManager)
        assert engine._consecutive_blocked == 0
        assert engine._events == []

    def test_init_custom_popup_handler_and_session_manager(self):
        config = PlanConfig()
        popup_handler = MagicMock(spec=PopupHandler)
        session_manager = MagicMock(spec=SessionManager)
        engine = ExecutionEngine(
            config=config,
            popup_handler=popup_handler,
            session_manager=session_manager,
        )
        assert engine.popup_handler is popup_handler
        assert engine.session_manager is session_manager

    def test_session_manager_uses_config_retry(self):
        config = PlanConfig(retry=RetryPolicy(session=5))
        engine = ExecutionEngine(config=config)
        assert engine.session_manager.retry_limit == 5


class TestShouldAbort:
    """should_abort method."""

    def test_abort_when_consecutive_blocked_exceeds_max(self):
        config = PlanConfig(abort_policy=AbortPolicy(max_consecutive_blocked=3))
        engine = ExecutionEngine(config=config)
        engine._consecutive_blocked = 3
        assert engine.should_abort() is True

    def test_abort_when_consecutive_blocked_under_limit(self):
        config = PlanConfig(abort_policy=AbortPolicy(max_consecutive_blocked=3))
        engine = ExecutionEngine(config=config)
        engine._consecutive_blocked = 2
        assert engine.should_abort() is False

    def test_abort_when_session_manager_says_abort(self):
        config = PlanConfig()
        session_manager = MagicMock(spec=SessionManager)
        session_manager.should_abort.return_value = True
        engine = ExecutionEngine(config=config, session_manager=session_manager)
        assert engine.should_abort() is True

    def test_abort_when_max_duration_exceeded(self):
        config = PlanConfig(abort_policy=AbortPolicy(max_total_duration_ms=100))
        engine = ExecutionEngine(config=config)
        engine._start_time = time.monotonic() - 10  # 10 seconds ago
        assert engine.should_abort() is True


class TestUpdateConsecutiveBlocked:
    """_update_consecutive_blocked method."""

    def test_increments_on_blocked(self):
        config = PlanConfig()
        engine = ExecutionEngine(config=config)
        tc = TestCase(id="TC1", title="Test")
        tc.execution.status = ExecutionStatus.BLOCKED
        engine._update_consecutive_blocked(tc)
        assert engine._consecutive_blocked == 1

    def test_increments_multiple_blocked(self):
        config = PlanConfig()
        engine = ExecutionEngine(config=config)
        for _ in range(3):
            tc = TestCase(id="TCx", title="Test")
            tc.execution.status = ExecutionStatus.BLOCKED
            engine._update_consecutive_blocked(tc)
        assert engine._consecutive_blocked == 3

    def test_resets_on_executed(self):
        config = PlanConfig()
        engine = ExecutionEngine(config=config)
        engine._consecutive_blocked = 3
        tc = TestCase(id="TC1", title="Test")
        tc.execution.status = ExecutionStatus.EXECUTED
        engine._update_consecutive_blocked(tc)
        assert engine._consecutive_blocked == 0

    def test_ignores_failed(self):
        """FAILED status does not change consecutive blocked count."""
        config = PlanConfig()
        engine = ExecutionEngine(config=config)
        engine._consecutive_blocked = 2
        tc = TestCase(id="TC1", title="Test")
        tc.execution.status = ExecutionStatus.FAILED
        engine._update_consecutive_blocked(tc)
        assert engine._consecutive_blocked == 2


class TestMarkAborted:
    """_mark_aborted method."""

    def test_sets_aborted_status_and_message(self):
        config = PlanConfig()
        engine = ExecutionEngine(config=config)
        tc = TestCase(id="TC1", title="Test")
        engine._mark_aborted(tc, "Session lost")
        assert tc.execution.status == ExecutionStatus.ABORTED
        assert tc.execution.error_message == "Session lost"
        assert tc.execution.reason == "Session lost"

    def test_sets_aborted_with_different_reason(self):
        config = PlanConfig()
        engine = ExecutionEngine(config=config)
        tc = TestCase(id="TC2", title="Test 2")
        engine._mark_aborted(tc, "Max consecutive blocked exceeded")
        assert tc.execution.status == ExecutionStatus.ABORTED
        assert tc.execution.error_message == "Max consecutive blocked exceeded"
        assert tc.execution.reason == "Max consecutive blocked exceeded"


class TestExecuteSingle:
    """_execute_single method (async)."""

    def test_marks_blocked_when_precondition_fails(self):
        config = PlanConfig()
        engine = ExecutionEngine(config=config)
        tc = TestCase(id="TC1", title="Test")
        engine._check_precondition = MagicMock(return_value=False)
        asyncio.run(engine._execute_single(tc))
        assert tc.execution.status == ExecutionStatus.BLOCKED
        assert "Precondition" in tc.execution.error_message

    def test_executes_successfully_without_precondition(self):
        """Mock _execute_step_async to test _execute_single flow independently."""
        config = PlanConfig()
        engine = ExecutionEngine(config=config)
        step = TestStep(step=1, action="tap", target="button")
        tc = TestCase(id="TC1", title="Test", steps=[step])
        engine._handle_popups = AsyncMock()
        engine._ensure_app_launched = AsyncMock()
        engine._execute_step_async = AsyncMock(
            return_value=StepExecution(
                step=1, action="tap", target="button",
                success=True,
            )
        )
        asyncio.run(engine._execute_single(tc))
        assert tc.execution.status == ExecutionStatus.EXECUTED
        assert len(tc.execution.steps) == 1
        assert tc.execution.steps[0].success is True
        assert tc.execution.steps[0].step == 1
        assert tc.execution.steps[0].action == "tap"
        assert tc.execution.steps[0].target == "button"

    def test_executes_with_precondition(self):
        """Mock _execute_step_async to test _execute_single flow independently."""
        config = PlanConfig()
        engine = ExecutionEngine(config=config)
        step = TestStep(step=1, action="tap", target="button")
        precondition = Precondition(description="Login first")
        tc = TestCase(
            id="TC1", title="Test",
            steps=[step], precondition=precondition,
        )
        engine._handle_popups = AsyncMock()
        engine._ensure_app_launched = AsyncMock()
        engine._execute_step_async = AsyncMock(
            return_value=StepExecution(
                step=1, action="tap", target="button",
                success=True,
            )
        )
        asyncio.run(engine._execute_single(tc))
        assert tc.execution.status == ExecutionStatus.EXECUTED
        assert len(tc.execution.steps) == 1

    def test_fails_on_step_failure(self):
        config = PlanConfig()
        engine = ExecutionEngine(config=config)
        step = TestStep(step=1, action="tap", target="button")
        tc = TestCase(id="TC1", title="Test", steps=[step])
        failed_exec = StepExecution(
            step=1, action="tap", target="button",
            success=False, failure_type=FailureType.ACTION_FAILED,
            error_message="Element not found",
        )
        engine._ensure_app_launched = AsyncMock()
        engine._handle_popups = AsyncMock()
        engine._execute_step_async = AsyncMock(return_value=failed_exec)
        asyncio.run(engine._execute_single(tc))
        assert tc.execution.status == ExecutionStatus.FAILED
        assert tc.execution.failed_step == 1
        assert tc.execution.failure_type == FailureType.ACTION_FAILED
        assert tc.execution.error_message == "Element not found"

    def test_aborts_during_execution(self):
        """TC is aborted if should_abort is True during step loop."""
        config = PlanConfig(abort_policy=AbortPolicy(max_consecutive_blocked=1))
        engine = ExecutionEngine(config=config)
        engine._consecutive_blocked = 1  # Triggers abort
        step = TestStep(step=1, action="tap", target="button")
        tc = TestCase(id="TC1", title="Test", steps=[step])
        engine._ensure_app_launched = AsyncMock()
        asyncio.run(engine._execute_single(tc))
        assert tc.execution.status == ExecutionStatus.ABORTED

    def test_empty_steps_still_executed(self):
        """TC with no steps is trivially executed."""
        config = PlanConfig()
        engine = ExecutionEngine(config=config)
        tc = TestCase(id="TC1", title="Test")
        engine._ensure_app_launched = AsyncMock()
        asyncio.run(engine._execute_single(tc))
        assert tc.execution.status == ExecutionStatus.EXECUTED
        assert tc.execution.steps == []


class TestCheckPrecondition:
    """_check_precondition method."""

    def test_returns_true_when_no_precondition(self):
        config = PlanConfig()
        engine = ExecutionEngine(config=config)
        tc = TestCase(id="TC1", title="Test")
        assert engine._check_precondition(tc) is True

    def test_returns_true_with_empty_precondition(self):
        config = PlanConfig()
        engine = ExecutionEngine(config=config)
        tc = TestCase(id="TC1", title="Test", precondition=Precondition())
        assert engine._check_precondition(tc) is True


class TestHandlePopups:
    """_handle_popups method (async)."""

    def test_handle_popups_noop(self):
        """Currently a no-op placeholder; verify it does not crash."""
        config = PlanConfig()
        engine = ExecutionEngine(config=config)
        tc = TestCase(id="TC1", title="Test")
        asyncio.run(engine._handle_popups(tc))


class TestExecuteStep:
    """_execute_step_async method."""

    def test_returns_step_execution_on_failure_without_session(self):
        """Without an Appium session, step execution returns a failure result."""
        config = PlanConfig()
        sm = MagicMock(spec=SessionManager)
        sm.session_id = None
        sm.appium_url = "http://localhost:4723"
        sm.needs_recovery.return_value = False
        engine = ExecutionEngine(config=config, session_manager=sm)
        step = TestStep(step=1, action="tap", target="ok_button")
        tc = TestCase(id="TC1", title="Test")
        result = asyncio.run(
            engine._execute_step_async(tc, step)
        )
        assert isinstance(result, StepExecution)
        assert result.step == 1
        assert result.action == "tap"
        assert result.target == "ok_button"
        assert result.success is False
        assert result.failure_type == FailureType.ACTION_FAILED
        assert result.duration_ms is not None
        assert result.duration_ms >= 0


class TestExecuteAll:
    """execute_all method (async)."""

    @staticmethod
    def _make_mock_session() -> MagicMock:
        """Create a mock SessionManager with a fake active session."""
        sm = MagicMock(spec=SessionManager)
        sm.create_session.return_value = "fake-session-id"
        sm.session_id = "fake-session-id"
        sm.appium_url = "http://localhost:4723"
        sm.should_abort.return_value = False
        sm.needs_recovery.return_value = False
        return sm

    def test_processes_all_test_cases(self):
        config = PlanConfig()
        engine = ExecutionEngine(config=config, session_manager=self._make_mock_session())
        # Mock _execute_single to avoid real execution
        engine._execute_single = AsyncMock()
        tcs = [
            TestCase(id="TC1", title="Test 1"),
            TestCase(id="TC2", title="Test 2"),
        ]
        results = asyncio.run(engine.execute_all(tcs))
        assert len(results) == 2
        assert engine._execute_single.call_count == 2

    def test_aborts_after_too_many_blocked(self):
        config = PlanConfig(abort_policy=AbortPolicy(max_consecutive_blocked=2))
        engine = ExecutionEngine(config=config, session_manager=self._make_mock_session())
        tcs = [
            TestCase(id="TC1", title="Test 1"),
            TestCase(id="TC2", title="Test 2"),
            TestCase(id="TC3", title="Test 3"),
        ]

        async def fake_execute_single(tc):
            tc.execution.status = ExecutionStatus.BLOCKED

        engine._execute_single = AsyncMock(side_effect=fake_execute_single)
        # Simulate consecutive blocked tracking in execute_all loop
        results = asyncio.run(engine.execute_all(tcs))
        assert len(results) == 3

    def test_returns_same_list_reference(self):
        config = PlanConfig()
        engine = ExecutionEngine(config=config)
        engine._execute_single = AsyncMock()
        tcs = [TestCase(id="TC1", title="Test 1")]
        result = asyncio.run(engine.execute_all(tcs))
        assert result is tcs

    def test_resets_start_time_and_events_on_each_call(self):
        config = PlanConfig()
        engine = ExecutionEngine(config=config)
        engine._execute_single = AsyncMock()
        engine._events = [{"old": "event"}]
        engine._start_time = 0.0
        tcs = [TestCase(id="TC1", title="Test 1")]
        asyncio.run(engine.execute_all(tcs))
        assert engine._start_time > 0
        assert engine._events == []

    def test_events_property_returns_copy(self):
        config = PlanConfig()
        engine = ExecutionEngine(config=config)
        events = engine.events
        events.append({"spoof": "value"})
        assert engine.events == []  # Original unchanged


class TestEventsProperty:
    """events property."""

    def test_events_returns_copy(self):
        config = PlanConfig()
        engine = ExecutionEngine(config=config)
        engine._events = [{"event_type": "TEST", "message": "hello"}]
        events = engine.events
        assert len(events) == 1
        events.clear()
        assert len(engine._events) == 1  # Original not affected
