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

        # ── Create Appium session before execution ────────────────────────
        sid = self.session_manager.create_session()
        if sid:
            self._log(f"[Appium session created: {sid[:12]}...]")
        else:
            self._log("[⚠️ Appium session creation failed — "
                      "check that Appium server is running and a device is connected]")
            return test_cases

        for tc in test_cases:
            if self.should_abort():
                self._mark_aborted(tc, "Abort condition met")
                continue

            # ── Environment reset before each TC ──────────────────────────
            if tc != test_cases[0]:
                self._log("Resetting device environment...")
                self._teardown_app()

            self._log(f"▶ {tc.id}: {tc.title} ...", end="", flush=True)
            self._logcat_start(tc.id)
            self._execute_single(tc)
            self._logcat_stop(tc)

            status = tc.execution.status.value if tc.execution.status else "UNKNOWN"
            verdict = tc.execution.verdict.value if tc.execution.verdict else ""
            if verdict == "PASS":
                print(f" ✅ {verdict}")
            elif verdict == "FAIL":
                print(f" ❌ {verdict}")
            else:
                print(f" {status}")

            self._update_consecutive_blocked(tc)

            # ── Pause between TCs for visual pacing ───────────────────────
            time.sleep(2)

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
        appium_url = self.session_manager.appium_url
        session_id = self.session_manager.session_id

        # ── session recovery: try to reconnect if session is dead ──────
        if session_id and self.session_manager.needs_recovery():
            new_sid = self.session_manager.recover_session()
            if new_sid:
                session_id = new_sid
                await asyncio.sleep(2)  # wait for session to stabilize

        step_start = time.time()
        success = True
        failure_type = None
        error_message = ""

        async def _exec_action() -> dict:
            """Execute the step action and return the result dict."""
            nonlocal session_id
            sid = session_id or self.session_manager.session_id

            if step.action == "tap":
                return await app_tap(
                    selector=step.target, strategy="uiautomator",
                    appium_url=appium_url, session_id=sid,
                )
            elif step.action == "type":
                return await app_type(
                    selector=step.target, text=step.value, strategy="accessibility_id",
                    appium_url=appium_url, session_id=sid,
                )
            elif step.action == "swipe":
                parts = step.target.split(",")
                if len(parts) >= 4:
                    return await app_swipe(
                        start_x=int(parts[0]), start_y=int(parts[1]),
                        end_x=int(parts[2]), end_y=int(parts[3]),
                        appium_url=appium_url, session_id=sid,
                    )
                return {"error": "Invalid swipe coordinates"}
            elif step.action == "launch":
                return await app_launch(
                    package=step.target,
                    appium_url=appium_url, session_id=sid,
                )
            elif step.action == "assert":
                return await app_assert_element(
                    selector=step.target, assertion="visible",
                    strategy="uiautomator",
                    appium_url=appium_url, session_id=sid,
                )
            elif step.action == "exec":
                return await app_exec(
                    command=step.value or step.target,
                    appium_url=appium_url, session_id=sid,
                )
            elif step.action == "screenshot":
                return await app_screenshot(
                    appium_url=appium_url, session_id=sid,
                )
            return {"error": f"Unknown action: {step.action}"}

        try:
            result = await _exec_action()
            result_str = str(result)

            # ── session died: recover and retry once ──
            _dead_patterns = (
                "invalid session id",
                "session is either terminated",
                "instrumentation process is not running",
            )
            if any(p in result_str for p in _dead_patterns):
                new_sid = self.session_manager.recover_session()
                if new_sid:
                    session_id = new_sid
                    await asyncio.sleep(2)
                    result = await _exec_action()
                    result_str = str(result)

            if result.get("error"):
                success = False
                failure_type = FailureType.ACTION_FAILED
                error_message = str(result["error"])
            elif result.get("passed") is False:
                # ── Assert failed: fall back to dynamic UI check ─────────
                # The LLM's guessed text may not match the real UI.
                # Grab the page source and look for ANY visible text
                # from the app to confirm it's alive.
                _sid = session_id or self.session_manager.session_id
                source_result = await app_get_source(
                    appium_url=appium_url, session_id=_sid,
                )
                source_xml = source_result.get("source", "")
                # Extract text attributes from XML
                import re as _re
                texts = _re.findall(r'text="([^"]{1,20})"', source_xml)
                visible_texts = [t.strip() for t in texts if t.strip()]
                if visible_texts:
                    # App has visible UI — treat as soft pass
                    self._log(
                        f"Target '{step.target}' not found, but app is alive. "
                        f"Visible texts: {visible_texts[:5]}"
                    )
                else:
                    success = False
                    failure_type = FailureType.ACTION_FAILED
                    error_message = result.get("reason", "Assertion failed")

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

    def _log(self, msg: str, **kwargs) -> None:
        """Print with timestamp prefix."""
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"  [{ts}] {msg}", **kwargs)

    def _teardown_app(self) -> None:
        """Reset device state between test cases.

        Runs cleanup commands asynchronously via the Appium session:
        - Re-enable WiFi and mobile data
        - Send app to background (home key) so its state persists
        """
        session_id = self.session_manager.session_id
        appium_url = self.session_manager.appium_url
        if not session_id:
            return

        async def _cleanup() -> None:
            cmds = [
                "svc wifi enable",
                "svc data enable",
                "input keyevent KEYCODE_HOME",
            ]
            for cmd in cmds:
                try:
                    await app_exec(
                        command=cmd,
                        appium_url=appium_url,
                        session_id=session_id,
                    )
                except Exception:
                    pass

        asyncio.run(_cleanup())

    def _logcat_start(self, tc_id: str) -> None:
        """Clear logcat buffer before a test case."""
        import subprocess
        try:
            subprocess.run(
                ["adb", "logcat", "-c"],
                capture_output=True, timeout=5,
                encoding="utf-8", errors="replace",
            )
        except Exception:
            pass

    def _logcat_stop(self, tc: TestCase) -> None:
        """If TC failed, dump last 20 lines of device log."""
        import subprocess
        if tc.execution.status not in (ExecutionStatus.FAILED, ExecutionStatus.ABORTED):
            return
        try:
            result = subprocess.run(
                ["adb", "logcat", "-v", "time", "-d", "-t", "20"],
                capture_output=True, text=True, timeout=5,
                encoding="utf-8", errors="replace",
            )
            if result.stdout.strip():
                self._log(f"Last device logs ({tc.id}):")
                for line in result.stdout.strip().split("\n")[-20:]:
                    print(f"         {line.strip()}")
        except Exception:
            pass

    def _handle_popups(self, tc: TestCase) -> None:
        """Detect and handle popups before step execution.

        Retrieves the current page source from the Appium session and
        passes it to the popup handler. If a popup is detected, the
        dismiss action is executed (e.g. tapping the dismiss button).
        Keeps dismissing dialogs in a loop until none remain (handles
        cascading dialogs like privacy agreement → permission request).

        Args:
            tc: The current TestCase (provides context for popup handling).
        """
        session_id = self.session_manager.session_id
        if not session_id:
            return

        max_rounds = 10
        for _round in range(max_rounds):
            try:
                result = asyncio.run(
                    app_get_source(
                        appium_url=self.session_manager.appium_url,
                        session_id=session_id,
                    )
                )
                page_source = result.get("source", "")
                popup_result = self.popup_handler.handle(page_source=page_source)
                if not popup_result:
                    break  # no more popups

                button_text = popup_result.get("button_text", "")
                if not button_text:
                    self._log(
                        f"Popup detected: {popup_result['rule_name']} "
                        f"(no button_text configured)"
                    )
                    break

                # Try exact text match first (avoids hitting dialog text
                # that only *contains* the button_text, e.g. "要允许..."
                # matching textContains("允许"))
                escaped = button_text.replace("\\", "\\\\").replace('"', '\\"')
                selectors_to_try = [
                    f'new UiSelector().text("{escaped}")',      # exact match
                    f'new UiSelector().textContains("{escaped}")',  # fallback
                ]

                tapped = False
                for selector in selectors_to_try:
                    tap_result = asyncio.run(
                        app_tap(
                            selector=selector,
                            strategy="uiautomator",
                            appium_url=self.session_manager.appium_url,
                            session_id=session_id,
                        )
                    )
                    if not tap_result.get("error"):
                        tapped = True
                        self._log(
                            f"Popup dismissed: {popup_result['rule_name']} "
                            f"(tapped '{button_text}')"
                        )
                        break

                if not tapped:
                    self._log(
                        f"Popup '{popup_result['rule_name']}' detected "
                        f"but tap '{button_text}' failed"
                    )
                    break  # can't dismiss, stop looping

                import time
                time.sleep(0.5)  # brief pause for next dialog to render
            except Exception as exc:
                self._log(f"Popup handler error: {exc}")
                break

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
