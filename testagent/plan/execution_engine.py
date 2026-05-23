from __future__ import annotations

import asyncio
import time
from datetime import datetime

from testagent.mcp_servers.appium_server.tools import (
    app_assert_element,
    app_exec,
    app_get_source,
    app_launch,
    app_screenshot,
    app_swipe,
    app_tap,
    app_type,
)
from testagent.plan.models import (
    ExecutionStatus,
    FailureType,
    PlanConfig,
    StepExecution,
    TestCase,
    TestStep,
)
from testagent.plan.popup_handler import PopupHandler
from testagent.plan.session_manager import SessionManager


class ExecutionEngine:
    """Executes test cases sequentially with shared session.

    The engine manages the full lifecycle of test case execution:
    - Sequential execution of test cases
    - Precondition checking with retry
    - Step-by-step execution with popup handling
    - Abort policy enforcement (consecutive blocks, duration, session)
    - Structured event recording
    """

    def __init__(
        self,
        config: PlanConfig,
        popup_handler: PopupHandler | None = None,
        session_manager: SessionManager | None = None,
    ) -> None:
        self.config = config
        self.popup_handler = popup_handler or PopupHandler()
        self.session_manager = session_manager or SessionManager(
            retry_limit=config.retry.session,
        )
        self._consecutive_blocked = 0
        self._start_time: float = 0.0
        self._events: list[dict] = []

    def should_abort(self) -> bool:
        """Check whether execution should abort based on the abort policy.

        Evaluates three conditions:
        1. Consecutive blocked test cases exceed the configured maximum.
        2. The session manager signals abort (recovery limit reached).
        3. Total execution duration exceeds the configured maximum.

        Returns:
            True if any abort condition is met, False otherwise.
        """
        policy = self.config.abort_policy

        if self._consecutive_blocked >= policy.max_consecutive_blocked:
            return True

        if self.session_manager.should_abort():
            return True

        if self._start_time > 0:
            elapsed_ms = (time.monotonic() - self._start_time) * 1000
            if elapsed_ms >= policy.max_total_duration_ms:
                return True

        return False

    def execute_all(self, test_cases: list[TestCase]) -> list[TestCase]:
        """Execute all test cases sequentially.

        Iterates through the provided test cases, checking abort conditions
        before each one. Test cases that pass are updated in-place with
        execution results (status, steps, errors).

        Args:
            test_cases: List of TestCase objects to execute.

        Returns:
            The same list of TestCase objects, with execution results populated.
        """
        self._events = []
        self._start_time = time.monotonic()

        for tc in test_cases:
            if self.should_abort():
                self._mark_aborted(tc, "Abort condition met")
                continue

            self._execute_single(tc)
            self._update_consecutive_blocked(tc)

        return test_cases

    def _execute_single(self, tc: TestCase) -> None:
        """Execute a single test case.

        Flow:
        1. Mark the test case as RUNNING.
        2. Check preconditions — if they fail, mark as BLOCKED.
        3. Execute each step in sequence, checking abort between steps.
        4. On step failure, mark as FAILED with the step's error details.
        5. If all steps pass, mark as EXECUTED.

        Args:
            tc: The TestCase to execute. Modified in-place.
        """
        tc.execution.status = ExecutionStatus.RUNNING
        tc.execution.error_message = ""

        if not self._check_precondition(tc):
            tc.execution.status = ExecutionStatus.BLOCKED
            tc.execution.error_message = "Precondition failed"
            return

        for step in tc.steps:
            if self.should_abort():
                self._mark_aborted(tc, "Abort during execution")
                return

            step_exec = self._execute_step(tc, step)
            tc.execution.steps.append(step_exec)

            if not step_exec.success:
                tc.execution.status = ExecutionStatus.FAILED
                tc.execution.failed_step = step.step
                tc.execution.failure_type = step_exec.failure_type
                tc.execution.error_message = step_exec.error_message
                return

        tc.execution.status = ExecutionStatus.EXECUTED

    def _execute_step(self, tc: TestCase, step: TestStep) -> StepExecution:
        """Execute a single test step.

        Handles popups before executing the step action, then dispatches
        to the async implementation via asyncio.run().

        Args:
            tc: The parent TestCase (used for context).
            step: The TestStep to execute.

        Returns:
            A StepExecution with the result of the step.
        """
        self._handle_popups(tc)
        return asyncio.run(self._execute_step_async(tc, step))

    async def _execute_step_async(self, tc: TestCase, step: TestStep) -> StepExecution:
        """Async implementation of step execution with real Appium calls.

        Maps step actions to the corresponding Appium MCP tool and returns
        a StepExecution capturing success/failure and timing.

        Args:
            tc: The parent TestCase (used for context).
            step: The TestStep to execute.

        Returns:
            A StepExecution with the result of the step.
        """
        session_id = self.session_manager.session_id
        appium_url = self.session_manager.appium_url

        step_start = time.time()
        success = True
        failure_type = None
        error_message = ""

        try:
            if step.action == "tap":
                result = await app_tap(
                    selector=step.target,
                    strategy="accessibility_id",
                    appium_url=appium_url,
                    session_id=session_id,
                )
                if result.get("error"):
                    success = False
                    failure_type = FailureType.ACTION_FAILED
                    error_message = str(result["error"])

            elif step.action == "type":
                result = await app_type(
                    selector=step.target,
                    text=step.value,
                    strategy="accessibility_id",
                    appium_url=appium_url,
                    session_id=session_id,
                )
                if result.get("error"):
                    success = False
                    failure_type = FailureType.ACTION_FAILED
                    error_message = str(result["error"])

            elif step.action == "swipe":
                # Format: "start_x,start_y,end_x,end_y"
                try:
                    parts = step.target.split(",")
                    if len(parts) >= 4:
                        result = await app_swipe(
                            start_x=int(parts[0]),
                            start_y=int(parts[1]),
                            end_x=int(parts[2]),
                            end_y=int(parts[3]),
                            appium_url=appium_url,
                            session_id=session_id,
                        )
                        if result.get("error"):
                            success = False
                            failure_type = FailureType.ACTION_FAILED
                            error_message = str(result["error"])
                    else:
                        success = False
                        failure_type = FailureType.ACTION_FAILED
                        error_message = "Invalid swipe coordinates: need start_x,start_y,end_x,end_y"
                except (ValueError, IndexError):
                    success = False
                    failure_type = FailureType.ACTION_FAILED
                    error_message = "Invalid swipe coordinate format"

            elif step.action == "launch":
                result = await app_launch(
                    package=step.target,
                    appium_url=appium_url,
                    session_id=session_id,
                )
                if result.get("error"):
                    success = False
                    failure_type = FailureType.ACTION_FAILED
                    error_message = str(result["error"])

            elif step.action == "assert":
                result = await app_assert_element(
                    selector=step.target,
                    assertion="visible",
                    appium_url=appium_url,
                    session_id=session_id,
                )
                if result.get("error"):
                    success = False
                    failure_type = FailureType.ASSERTION_FAILED
                    error_message = str(result["error"])
                elif not result.get("passed", True):
                    success = False
                    failure_type = FailureType.ASSERTION_FAILED
                    error_message = result.get("reason", "Assertion failed")

            elif step.action == "exec":
                result = await app_exec(
                    command=step.value or step.target,
                    appium_url=appium_url,
                    session_id=session_id,
                )
                if result.get("error"):
                    success = False
                    failure_type = FailureType.ACTION_FAILED
                    error_message = str(result["error"])

            elif step.action == "screenshot":
                result = await app_screenshot(
                    appium_url=appium_url,
                    session_id=session_id,
                )
                if result.get("error"):
                    success = False
                    failure_type = FailureType.SCREENSHOT_FAILED
                    error_message = str(result["error"])

            else:
                success = False
                failure_type = FailureType.ACTION_FAILED
                error_message = f"Unknown action: {step.action}"

        except Exception as e:
            success = False
            failure_type = FailureType.ACTION_FAILED
            error_message = str(e)

        elapsed = int((time.time() - step_start) * 1000)
        return StepExecution(
            step=step.step,
            action=step.action,
            target=step.target,
            success=success,
            failure_type=failure_type,
            error_message=error_message,
            duration_ms=elapsed,
        )

    def _check_precondition(self, tc: TestCase) -> bool:
        """Check and execute precondition setup with retry.

        If the test case has no precondition, trivially returns True.
        Otherwise attempts to execute the precondition setup steps,
        retrying up to ``precondition.max_retries`` times.

        Args:
            tc: The TestCase whose precondition should be checked.

        Returns:
            True if the precondition passes (or is absent), False otherwise.
        """
        if tc.precondition is None:
            return True

        max_retries = tc.precondition.max_retries
        for attempt in range(max_retries + 1):
            try:
                # TODO: Execute precondition setup steps via driver
                return True
            except Exception:
                if attempt < max_retries:
                    continue
                tc.execution.error_message = (
                    f"Precondition failed after {max_retries + 1} attempts"
                )
                return False

        return False

    def _handle_popups(self, tc: TestCase) -> None:
        """Detect and handle popups before step execution.

        Retrieves the current page source from the Appium session and
        passes it to the popup handler. If a popup is detected, an event
        is recorded.

        Args:
            tc: The current TestCase (provides context for popup handling).
        """
        session_id = self.session_manager.session_id
        if not session_id:
            return
        try:
            result = asyncio.run(
                app_get_source(
                    appium_url=self.session_manager.appium_url,
                    session_id=session_id,
                )
            )
            page_source = result.get("source", "")
            popup_result = self.popup_handler.handle(page_source=page_source)
            if popup_result:
                self._add_event(
                    event_type="INFO",
                    tc_id=tc.id,
                    message=f"Popup handled: {popup_result['rule_name']}",
                )
        except Exception:
            pass

    def _mark_aborted(self, tc: TestCase, reason: str) -> None:
        """Mark a test case as aborted with a reason.

        Args:
            tc: The TestCase to mark as aborted.
            reason: A human-readable reason for the abort.
        """
        tc.execution.status = ExecutionStatus.ABORTED
        tc.execution.error_message = reason
        tc.execution.reason = reason

    def _update_consecutive_blocked(self, tc: TestCase) -> None:
        """Track consecutive blocked test cases.

        Increments the counter when a test case ends in BLOCKED status.
        Resets the counter to zero when a test case executes successfully,
        breaking the blocked chain.

        Args:
            tc: The TestCase that just finished execution.
        """
        if tc.execution.status == ExecutionStatus.BLOCKED:
            self._consecutive_blocked += 1
        elif tc.execution.status == ExecutionStatus.EXECUTED:
            self._consecutive_blocked = 0

    def _add_event(
        self,
        event_type: str,
        tc_id: str = "",
        message: str = "",
        **kwargs,
    ) -> None:
        """Add a structured event to the internal events list.

        Args:
            event_type: The type/category of the event.
            tc_id: Optional test case identifier.
            message: A human-readable message for the event.
            **kwargs: Additional key-value pairs to include in the event dict.
        """
        self._events.append({
            "time": datetime.now(),
            "event_type": event_type,
            "tc_id": tc_id,
            "message": message,
            **kwargs,
        })

    @property
    def events(self) -> list[dict]:
        """Return a copy of the internal events list."""
        return list(self._events)
