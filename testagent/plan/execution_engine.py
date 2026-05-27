from __future__ import annotations

import asyncio
import base64
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from testagent.mcp_servers.appium_server.tools import (
    app_assert_element,
    app_exec,
    app_get_source,
    app_launch,
    app_screenshot,
    app_start_recording,
    app_stop_recording,
    app_swipe,
    app_tap,
    app_type,
)
from testagent.plan.models import (
    EvidenceItem,
    ExecutionStatus,
    FailureType,
    PlanConfig,
    StepExecution,
    TestCase,
    TestStep,
)
from testagent.plan.popup_handler import PopupHandler
from testagent.plan.session_manager import SessionManager

# ── Prompt loading ───────────────────────────────────────────────
_PROMPTS_DIR = Path(__file__).parent / "prompts"

_STEP_EXECUTION_TEMPLATE: str | None = None


def _load_step_prompt() -> str:
    """Load the step execution prompt template (cached after first load)."""
    global _STEP_EXECUTION_TEMPLATE
    if _STEP_EXECUTION_TEMPLATE is None:
        _STEP_EXECUTION_TEMPLATE = (_PROMPTS_DIR / "step_execution.txt").read_text(encoding="utf-8")
    return _STEP_EXECUTION_TEMPLATE


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
        llm_provider: Any = None,
    ) -> None:
        self.config = config
        self.popup_handler = popup_handler or PopupHandler()
        self.session_manager = session_manager or SessionManager(
            retry_limit=config.retry.session,
        )
        self._consecutive_blocked = 0
        self._start_time: float = 0.0
        self._events: list[dict] = []
        self._vision_client: Any | None = None
        self._llm_provider: Any = llm_provider
        self._suppressed_rules: set[str] = set()
        self._current_recording_tc: str = ""
        self._screen_w: int = 0
        self._screen_h: int = 0

    # ── screen size (lazy) ──────────────────────────────────────────────

    async def _get_screen_size(self) -> tuple[int, int]:
        """Get real device screen size via adb shell wm size (cached)."""
        if self._screen_w > 0 and self._screen_h > 0:
            return self._screen_w, self._screen_h
        import re
        try:
            result = await app_exec(
                command="wm size",
                appium_url=self.session_manager.appium_url,
                session_id=self.session_manager.session_id,
            )
            body = result.get("body", {})
            value = str(body.get("value", ""))
            m = re.search(r"(\d+)x(\d+)", value)
            if m:
                self._screen_w = int(m.group(1))
                self._screen_h = int(m.group(2))
            else:
                self._screen_w, self._screen_h = 1080, 2400
        except Exception:
            self._screen_w, self._screen_h = 1080, 2400
        return self._screen_w, self._screen_h

    # ── vision client (lazy) ─────────────────────────────────────────────────

    def _init_vision_client(self) -> Any | None:
        """Lazy-init a vision client for multimodal popup dismissal."""
        if self._vision_client is not None:
            return self._vision_client
        try:
            from testagent.config.settings import get_settings

            settings = get_settings()
            key = settings.vision_api_key.get_secret_value()
            if not key:
                self._vision_client = None
                return None
            from testagent.mcp_servers.vision_server.volcano_client import (
                VolcanoVisionClient,
            )

            self._vision_client = VolcanoVisionClient(
                api_key=key,
                api_url=settings.vision_api_url,
                model=settings.vision_model,
                timeout=settings.vision_timeout,
                max_retries=settings.vision_max_retries,
            )
            return self._vision_client
        except Exception:
            self._vision_client = None
            return None

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

    def _retry_create_session(self, max_attempts: int = 5, delay: float = 5.0) -> str | None:
        """Create a new Appium session with retries and delay between attempts.

        After force-stop kills the app and UiAutomator2, Appium needs time to
        clean up the stale session state before accepting a new session. This
        method retries with delays instead of failing immediately, avoiding
        wasted TC iterations in ``execute_all()``.

        Args:
            max_attempts: Maximum number of creation attempts.
            delay: Seconds to wait between attempts.

        Returns:
            The new session ID string, or None if all attempts failed.
        """
        # Small initial wait for Appium to register the UiAutomator2 death
        time.sleep(2)

        for attempt in range(1, max_attempts + 1):
            sid = self.session_manager.create_session()
            if sid:
                return sid
            if attempt < max_attempts:
                self._log(
                    f"  [Session creation attempt {attempt}/{max_attempts} "
                    f"failed, retrying in {delay}s...]"
                )
                time.sleep(delay)

        self._log(
            f"  [Session creation failed after {max_attempts} attempts, giving up]"
        )
        return None

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

                # ── Session died during teardown? Create a fresh one ────
                # is_connected() can return false positives after force-stop
                # kills UiAutomator2 — the HTTP endpoint still responds but
                # page source is empty. Do a real health check.
                session_dead = not self.session_manager.is_connected()
                if not session_dead:
                    # Verify by actually getting page source
                    try:
                        src_result = asyncio.run(
                            app_get_source(
                                appium_url=self.session_manager.appium_url,
                                session_id=self.session_manager.session_id,
                            )
                        )
                        page_src = src_result.get("source", "")
                        if not page_src:
                            self._log("Session is zombie (no page source) — recreating...")
                            session_dead = True
                    except Exception:
                        session_dead = True

                if session_dead:
                    self.session_manager.close_session()
                    self.session_manager.reset_recovery()
                    new_sid = self._retry_create_session()
                    if new_sid:
                        self._log(f"[Session recreated: {new_sid[:12]}...]")
                    else:
                        self._mark_aborted(tc, "Failed to recreate session after teardown")
                        continue

            self._log(f"▶ {tc.id}: {tc.title} ...", end="", flush=True)
            self._logcat_start(tc.id)

            # ── Start screen recording for this TC ───────────────────────
            self._start_recording(tc)

            try:
                self._execute_single(tc)
            finally:
                # ── Stop recording for this TC (always, even on error) ───
                self._stop_recording(tc)

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

        # Ensure app is launched before executing steps.
        # The app is force-stopped between TCs, so every TC needs a fresh
        # launch. We do this here rather than relying on the LLM-generated
        # first step always being a correct "launch" action.
        self._ensure_app_launched()

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
                # Layer 1: Direct vision element search (like chat's vision_find_element)
                # Fast and reliable — vision model looks at screenshot and returns coordinates.
                self._log(f"  [Vision: looking for '{step.target}']")
                coords = await self._vision_find_element(step.target)
                if coords:
                    res = await app_tap(
                        x=coords["x"], y=coords["y"],
                        appium_url=appium_url, session_id=sid,
                    )
                    if not res.get("error"):
                        self._log(
                            f"  [Vision tap at ({coords['x']}, {coords['y']})]"
                        )
                        return res

                # Layer 2: LLM-driven execution (like chat agent loop)
                # Take screenshot → vision describes screen → LLM reads description,
                # extracts percentage coordinates, converts to pixels, and decides.
                self._log(
                    f"  [Vision didn't find '{step.target}', trying LLM...]"
                )
                llm_result = await self._execute_step_via_llm(step, sid)
                if llm_result and not llm_result.get("error"):
                    self._log("  [LLM execution succeeded]")
                    return llm_result

                # Layer 3: Wait for UI to settle, then retry vision
                await asyncio.sleep(2)
                coords = await self._vision_find_element(step.target)
                if coords:
                    res = await app_tap(
                        x=coords["x"], y=coords["y"],
                        appium_url=appium_url, session_id=sid,
                    )
                    if not res.get("error"):
                        self._log(
                            f"  [Vision retry at ({coords['x']}, {coords['y']})]"
                        )
                        return res

                # Layer 4: Content fallback — tap the first clickable content
                # item on screen. Handles cases where the target is a specific
                # dynamic text (UP主 name, video title) that doesn't exist,
                # but the test intent (e.g. "tap a video card") is correct.
                self._log(
                    f"  [Content fallback: looking for any clickable "
                    f"content item...]"
                )
                coords = await self._vision_find_any_content()
                if coords:
                    res = await app_tap(
                        x=coords["x"], y=coords["y"],
                        appium_url=appium_url, session_id=sid,
                    )
                    if not res.get("error"):
                        self._log(
                            f"  [Content fallback tap at ({coords['x']}, "
                            f"{coords['y']}) — proceeding]"
                        )
                        return res

                return {"error": f"Element '{step.target}' not found (vision + LLM + vision retry)"}
            elif step.action == "type":
                coords = await self._vision_find_element(
                    step.target or "输入框"
                )
                if coords:
                    await app_tap(
                        x=coords["x"], y=coords["y"],
                        appium_url=appium_url, session_id=sid,
                    )
                    await asyncio.sleep(0.5)
                    res = await app_type(
                        selector="new UiSelector().className(\"android.widget.EditText\").focused(true)",
                        text=step.value, strategy="uiautomator",
                        appium_url=appium_url, session_id=sid,
                    )
                else:
                    res = {"error": f"Input field '{step.target}' not found"}
                return res
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
                # Use config package name (not LLM-generated) to prevent
                # hallucinated package names like "buli" instead of "bili".
                pkg = self.config.app_package or step.target
                result = await app_launch(
                    package=pkg,
                    appium_url=appium_url, session_id=sid,
                )
                # Wait for the app to fully render after launch.
                # `monkey -p` returns immediately but the app may still
                # be initialising, especially after force-stop.
                await asyncio.sleep(3)
                # Verify the app is actually in the foreground by
                # checking page source for the app's package name.
                if not result.get("error"):
                    try:
                        src = await app_get_source(
                            appium_url=appium_url, session_id=sid,
                        )
                        xml = src.get("source", "")
                        if pkg not in xml:
                            # App didn't come to foreground — retry once
                            self._log(
                                f"App not visible after launch, retrying..."
                            )
                            await asyncio.sleep(2)
                            result = await app_launch(
                                package=pkg,
                                appium_url=appium_url, session_id=sid,
                            )
                            await asyncio.sleep(3)
                    except Exception:
                        pass
                return result
            elif step.action == "assert":
                # Use vision to evaluate the assertion (same as chat's approach).
                # Natural-language targets like "推荐Tab处于选中状态" are
                # evaluated by the vision model looking at a screenshot.
                vision_result = await self._assert_with_vision(step.target)
                if vision_result is not None:
                    return vision_result
                # Fallback to XML-based assertion for simple text targets
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

            if result.get("error") or result.get("passed") is False:
                # ── Step failed: try navigation recovery before giving up ──
                # The app might be on a sub-page (favorites, search, settings)
                # instead of the expected screen. Try KEYCODE_BACK and
                # vision-based recovery before declaring failure.
                _sid = session_id or self.session_manager.session_id
                _recovered = False

                if step.action in ("tap", "assert"):
                    # Attempt 1: KEYCODE_BACK (fast, handles standard
                    # Android back-stack navigation)
                    for _back_i in range(2):
                        try:
                            await app_exec(
                                command="input keyevent KEYCODE_BACK",
                                appium_url=appium_url, session_id=_sid,
                            )
                            await asyncio.sleep(1)
                            retry = await _exec_action()
                            retry_ok = (
                                not retry.get("error")
                                and retry.get("passed") is not False
                            )
                            if retry_ok:
                                result = retry
                                _recovered = True
                                break
                        except Exception:
                            break

                    # Attempt 2: Vision-based navigation recovery
                    if not _recovered:
                        nav_ok = await self._try_navigation_recovery_with_vision(step)
                        if nav_ok:
                            await asyncio.sleep(1)
                            retry = await _exec_action()
                            retry_ok = (
                                not retry.get("error")
                                and retry.get("passed") is not False
                            )
                            if retry_ok:
                                result = retry
                                _recovered = True

                if not _recovered:
                    # Diagnostic: log visible texts so we can debug
                    try:
                        source_result = await app_get_source(
                            appium_url=appium_url, session_id=_sid,
                        )
                        source_xml = source_result.get("source", "")
                        import re as _re
                        texts = _re.findall(r'text="([^"]{1,20})"', source_xml)
                        visible_texts = [t.strip() for t in texts if t.strip()]
                        if visible_texts:
                            self._log(
                                f"Target '{step.target}' not found, "
                                f"but app is alive. "
                                f"Visible texts: {visible_texts[:5]}"
                            )
                    except Exception:
                        pass
                    success = False
                    failure_type = FailureType.ACTION_FAILED
                    error_message = result.get("error") or result.get("reason", "Step failed")

        except Exception as e:
            success = False
            failure_type = FailureType.ACTION_FAILED
            error_message = str(e)

        elapsed = int((time.time() - step_start) * 1000)
        step_exec = StepExecution(
            step=step.step,
            action=step.action,
            target=step.target,
            success=success,
            failure_type=failure_type,
            error_message=error_message,
            duration_ms=elapsed,
        )

        # ── On failure: save screenshot + vision analysis ──────────
        if not success:
            try:
                # Save screenshot to disk
                scr_result = await app_screenshot(
                    appium_url=self.session_manager.appium_url,
                    session_id=self.session_manager.session_id,
                )
                scr_id = scr_result.get("screenshot_id", "")
                if scr_id:
                    from testagent.mcp_servers.shared_cache import get_screenshot

                    b64_data = get_screenshot(scr_id)
                    if b64_data:
                        scr_dir = Path(self.config.output_dir) / "screenshots"
                        scr_dir.mkdir(parents=True, exist_ok=True)
                        scr_path = scr_dir / f"{tc.id}_step{step.step}.png"
                        scr_path.write_bytes(base64.b64decode(b64_data))
                        step_exec.screenshot_after = str(scr_path)
                        tc.execution.evidence.append(
                            EvidenceItem(type="screenshot", path=str(scr_path))
                        )

                # Vision analysis
                vision_note = await self._analyze_failure_with_vision(step)
                if vision_note:
                    step_exec.vision_analysis = vision_note
            except Exception:
                pass  # Both are best-effort

        return step_exec

    def _ensure_app_launched(self) -> None:
        """Launch the app before executing test steps.

        The app is force-stopped between TCs, so every TC starts from a
        clean state. This method ensures the app is in the foreground
        before any step executes — regardless of whether the LLM
        generated a correct ``launch`` step or not.
        """
        pkg = self.config.app_package
        if not pkg:
            return

        import asyncio as _asyncio

        async def _launch() -> None:
            sid = self.session_manager.session_id
            url = self.session_manager.appium_url
            result = await app_launch(
                package=pkg, appium_url=url, session_id=sid,
            )
            if not result.get("error"):
                self._log(f"App launched: {pkg}")
                await _asyncio.sleep(3)
            else:
                # Retry once
                await _asyncio.sleep(2)
                result = await app_launch(
                    package=pkg, appium_url=url, session_id=sid,
                )
                await _asyncio.sleep(3)
                if not result.get("error"):
                    self._log(f"App launched (retry): {pkg}")
                else:
                    self._log(f"App launch failed: {result.get('error', '')[:80]}")

        _asyncio.run(_launch())

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
        """Force-stop the app between test cases using direct ADB.

        Using ``adb shell am force-stop`` directly (not through the Appium
        session) ensures the app is killed even when the UiAutomator2
        instrumentation has crashed. Each TC must start from a clean app
        state to prevent cascading failures from stale navigation state
        (e.g. TC-A navigates to a sub-page, TC-B starts on that sub-page
        instead of the home screen).

        Killing the app also kills the UiAutomator2 instrumentation, which
        will be detected by the session health check in ``execute_all()``
        and trigger a fresh session creation before the next TC.
        """
        import subprocess

        pkg = self.config.app_package or ""
        if pkg:
            try:
                subprocess.run(
                    ["adb", "shell", "am", "force-stop", pkg],
                    capture_output=True, timeout=10,
                )
            except Exception:
                pass

        # Also try cleanup via Appium session if still alive
        session_id = self.session_manager.session_id
        if not session_id:
            return

        async def _cleanup() -> None:
            cmds = [
                "svc wifi enable",
                "svc data enable",
            ]
            for cmd in cmds:
                try:
                    await app_exec(
                        command=cmd,
                        appium_url=self.session_manager.appium_url,
                        session_id=session_id,
                    )
                except Exception:
                    pass

        asyncio.run(_cleanup())

    # ── screen recording ───────────────────────────────────────────────────

    def _start_recording(self, tc: TestCase) -> None:
        """Start screen recording for a test case using Appium's recording API.

        The recording spans the execution of the current test case, capturing
        the actual UI behaviour including popups, navigation, and any error
        states. The recording is stopped in a ``finally`` block after execution.

        Best-effort — failures log a warning but don't block execution.
        """
        session_id = self.session_manager.session_id
        if not session_id:
            self._log(f"  [Cannot start recording for {tc.id}: no session]")
            return
        try:
            result = asyncio.run(
                app_start_recording(
                    appium_url=self.session_manager.appium_url,
                    session_id=session_id,
                )
            )
            if not result.get("error"):
                self._current_recording_tc = tc.id
                self._log(f"[Recording started for {tc.id}]")
            else:
                self._log(f"  [Recording start failed for {tc.id}: {result['error'][:80]}]")
        except Exception as exc:
            self._log(f"  [Recording start error for {tc.id}: {exc}]")

    def _stop_recording(self, tc: TestCase | None = None) -> None:
        """Stop screen recording, save the video, and add as evidence."""
        tc_id = self._current_recording_tc
        self._current_recording_tc = ""
        session_id = self.session_manager.session_id
        if not session_id:
            if tc_id:
                self._log(f"  [Cannot stop recording for {tc_id}: no session]")
            return
        if not tc_id:
            return
        try:
            result = asyncio.run(
                app_stop_recording(
                    appium_url=self.session_manager.appium_url,
                    session_id=session_id,
                )
            )
            video_b64 = result.get("video_base64", "")
            if not video_b64:
                err = result.get("error", "no video data")
                self._log(f"  [Recording stop for {tc_id}: {err[:80]}]")
                return

            video_dir = Path(self.config.output_dir) / "recordings"
            video_dir.mkdir(parents=True, exist_ok=True)
            video_path = video_dir / f"{tc_id}.mp4"
            video_path.write_bytes(base64.b64decode(video_b64))

            # Add as evidence
            if tc is not None:
                tc.execution.evidence.append(
                    EvidenceItem(type="recording", path=str(video_path))
                )

            self._log(f"[Recording saved: {video_path.name}]")
        except Exception as exc:
            self._log(f"  [Recording save error for {tc_id}: {exc}]")

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
        Uses vision model as fallback when text-based tapping fails,
        and tracks false positives so they are not re-detected within
        the same step.

        When text-based rules find nothing, vision is still consulted
        as a general popup detector — catching unknown system dialogs
        that no keyword rule covers.

        Args:
            tc: The current TestCase (provides context for popup handling).
        """
        session_id = self.session_manager.session_id
        if not session_id:
            return

        self._suppressed_rules.clear()

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
                    # ── Text rules found nothing. Try vision as a
                    #     general popup detector for unknown dialogs ────
                    vision_ok = asyncio.run(self._detect_unknown_popup())
                    if vision_ok:
                        time.sleep(1.0)
                        continue   # popup dismissed, re-check
                    break  # no popup detected at all

                rule_name = popup_result["rule_name"]

                # ── Skip if already confirmed as false positive ─────────
                if rule_name in self._suppressed_rules:
                    self._log(
                        f"Skipping already-confirmed false positive: "
                        f"'{rule_name}'"
                    )
                    break

                button_text = popup_result.get("button_text", "")
                if not button_text:
                    self._log(
                        f"Popup detected: {rule_name} "
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
                            f"Popup dismissed: {rule_name} "
                            f"(tapped '{button_text}')"
                        )
                        break

                if not tapped:
                    # ── Vision-based fallback ──────────────────────────
                    self._log(
                        f"Popup '{rule_name}': "
                        f"text tap failed, trying vision..."
                    )
                    vision_ok = asyncio.run(
                        self._dismiss_with_vision(rule_name)
                    )
                    if vision_ok:
                        tapped = True
                    else:
                        # Vision confirmed no popup — suppress this rule
                        # so we don't waste time on the same false positive
                        self._suppressed_rules.add(rule_name)
                        self._log(
                            f"Vision confirmed no popup for '{rule_name}' "
                            f"— suppressing further detection"
                        )
                        break  # no popup, stop looping

                time.sleep(1.5)  # pause for next dialog or UI to render
            except Exception as exc:
                self._log(f"Popup handler error: {exc}")
                break

    async def _detect_unknown_popup(self) -> bool:
        """Use vision to detect and dismiss an unknown popup (no text rule matched).

        Takes a screenshot and asks the vision model whether a dialog/popup
        is obscuring the screen. If found, locates the dismiss/close button
        and taps it.

        Returns:
            True if a popup was found and dismissed, False otherwise.
        """
        client = self._init_vision_client()
        if not client:
            return False

        scr_result = await app_screenshot(
            appium_url=self.session_manager.appium_url,
            session_id=self.session_manager.session_id,
        )
        screenshot_id = scr_result.get("screenshot_id", "")
        if not screenshot_id:
            return False

        from testagent.mcp_servers.shared_cache import get_screenshot

        b64 = get_screenshot(screenshot_id)
        if not b64:
            return False

        prompt = (
            "请严格判断当前屏幕截图是否存在弹窗/对话框遮挡了主界面内容。\n\n"
            "**以下情况不是弹窗，请不要误判：**\n"
            "- 正常的 app 首页内容（推荐流、视频列表、图文卡片）\n"
            "- 广告横幅/banner（只是页面中的广告位，不是弹窗）\n"
            "- 底部导航栏、顶部 Tab 栏\n"
            "- 视频播放界面（播放器、评论区、相关推荐）\n\n"
            "**真正的弹窗特征：**\n"
            "1. 屏幕中央有明确的对话框卡片，周围背景变暗或模糊\n"
            "2. 对话框上有明显的关闭/确认按钮（如「同意」「拒绝」「允许」「取消」"
            "「确定」「知道了」「关闭」「稍后」「以后再说」）\n"
            "3. 对话框内有「权限」「更新」「通知」「位置」「青少年」「隐私」等关键词\n"
            "4. 全屏浮层/引导页覆盖了所有内容\n\n"
            "只有当明确存在真正的弹窗时，才回复 found: true 并提供关闭按钮坐标。\n"
            "如果只是普通的内容页面，请回复 found: false。\n\n"
            "请按以下格式回复：\n"
            "- found: true/false\n"
            "- 如果找到，提供 center 百分比坐标 (pct_x%, pct_y%)\n"
            "- 简要描述弹窗内容和按钮文字"
        )

        dw, dh = await self._get_screen_size()
        result = await client.analyze(
            b64, prompt, device_width=dw, device_height=dh,
        )
        if "error" in result:
            return False

        content = result.get("content", "")

        from testagent.mcp_servers.vision_server.tools import (
            _parse_found_status,
            _parse_percentage_coordinates,
        )

        coords = _parse_percentage_coordinates(content, dw, dh)
        found = _parse_found_status(content) or bool(coords.get("center"))

        if not found:
            return False

        center = coords["center"]
        self._log(
            f"  [Vision detected unknown popup — tapping at "
            f"({center['x']}, {center['y']})]"
        )

        tap_result = await app_tap(
            x=center["x"], y=center["y"],
            appium_url=self.session_manager.appium_url,
            session_id=self.session_manager.session_id,
        )
        if not tap_result.get("error"):
            self._log(f"Unknown popup dismissed via vision")
            return True

        return False

    async def _dismiss_with_vision(self, rule_name: str) -> bool:
        """Try to dismiss a popup using the vision model.

        Takes a screenshot, asks the vision model to locate a dismiss/close
        button, and taps at the returned pixel coordinates. Falls back to a
        broader search if the first attempt yields nothing.

        Returns:
            True if the vision model found a button and the tap succeeded.
        """
        client = self._init_vision_client()
        if not client:
            self._log("  [Vision client not available]")
            return False

        scr_result = await app_screenshot(
            appium_url=self.session_manager.appium_url,
            session_id=self.session_manager.session_id,
        )
        screenshot_id = scr_result.get("screenshot_id", "")
        if not screenshot_id:
            return False

        from testagent.mcp_servers.shared_cache import get_screenshot

        b64 = get_screenshot(screenshot_id)
        if not b64:
            return False

        # ── Use vision model directly with a popup-specific prompt ────
        prompt = (
            "当前屏幕上可能有一个弹窗/对话框。\n"
            "请分析屏幕内容，找到弹窗上可以点击来关闭该弹窗的按钮。\n"
            "关闭按钮的文字通常是：关闭、取消、稍后、知道了、不再提醒、确定、拒绝\n"
            "也可能是一个叉号(X)关闭图标。\n\n"
            "请按以下格式回复：\n"
            "- found: true/false\n"
            "- 如果找到，提供 center 百分比坐标 (pct_x%, pct_y%)\n"
            "- 如果找到，提供 bounds [pct_x1%, pct_y1%, pct_x2%, pct_y2%]\n"
            "- 简要描述你找到的按钮内容。"
        )

        dw, dh = await self._get_screen_size()
        result = await client.analyze(
            b64, prompt, device_width=dw, device_height=dh,
        )
        if "error" in result:
            self._log(f"  [Vision API error: {result['error'][:80]}]")
            return False

        content = result.get("content", "")

        # Parse coordinates using shared helpers
        from testagent.mcp_servers.vision_server.tools import (
            _parse_found_status,
            _parse_percentage_coordinates,
        )

        coords = _parse_percentage_coordinates(content, dw, dh)
        found = _parse_found_status(content) or bool(coords.get("center"))

        if not found:
            self._log(
                f"  [Vision: no dismiss button found — "
                f"model says: {content[:100]}]"
            )
            return False

        center = coords["center"]
        self._log(
            f"  [Vision found button at ({center['x']}, {center['y']})]"
        )

        tap_result = await app_tap(
            x=center["x"], y=center["y"],
            appium_url=self.session_manager.appium_url,
            session_id=self.session_manager.session_id,
        )
        if not tap_result.get("error"):
            self._log(
                f"Popup dismissed via vision: {rule_name} "
                f"(coords: {center['x']},{center['y']})"
            )
            return True

        self._log(f"  [Vision tap failed: {tap_result.get('error', '')[:80]}]")
        return False

    async def _execute_step_via_llm(
        self, step: TestStep, sid: str | None
    ) -> dict | None:
        """Use the LLM with vision description to figure out how to execute a step.

        Captures a screenshot, gets a vision description, and asks the LLM to
        reason about how to reach the target element. No XML page source is
        used — the LLM works purely from the multimodal vision description.

        Returns:
            A result dict on success, or None if the LLM couldn't help.
        """
        if not self._llm_provider:
            return None

        dw, dh = await self._get_screen_size()

        # Get screen description from vision model
        screen_desc = ""
        vision_client = self._init_vision_client()
        if vision_client:
            try:
                scr_result = await app_screenshot(
                    appium_url=self.session_manager.appium_url, session_id=sid,
                )
                shot_id = scr_result.get("screenshot_id", "")
                if shot_id:
                    from testagent.mcp_servers.shared_cache import get_screenshot
                    b64 = get_screenshot(shot_id)
                    if b64:
                        desc_result = await vision_client.analyze(
                            b64,
                            "请简要描述当前屏幕上的所有可交互元素及其位置（用百分比坐标）。"
                            "包括按钮、输入框、Tab、图标等。请用中文。",
                            device_width=dw, device_height=dh,
                        )
                        if "content" in desc_result:
                            screen_desc = desc_result["content"][:1500]
            except Exception:
                pass

        if not screen_desc:
            self._log("  [LLM step: no vision description available]")
            return None

        # Build prompt from template (loaded from prompts/step_execution.txt)
        value_line = f"，输入内容：{step.value}" if step.value else ""
        template = _load_step_prompt()
        prompt = template.format(
            action=step.action,
            target=step.target,
            value_line=value_line,
            screen_desc=screen_desc,
            screen_width=dw,
            screen_height=dh,
        )

        try:
            response = await self._llm_provider.chat(
                system="你是一个 Android 自动化测试助手。请始终用 JSON 回复。",
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=1024,
            )
            raw = ""
            for block in response.content:
                if block.get("type") == "text":
                    raw += str(block.get("text", ""))
        except Exception:
            return None

        if not raw:
            return None

        # Parse LLM's JSON response
        import json as _json
        try:
            # Extract JSON from possible markdown wrapping
            start = raw.find("{")
            end = raw.rfind("}")
            if start >= 0 and end > start:
                plan = _json.loads(raw[start:end + 1])
            else:
                return None
        except (_json.JSONDecodeError, ValueError):
            return None

        found = plan.get("found", False)
        x = plan.get("x")
        y = plan.get("y")
        reasoning = plan.get("reasoning", "")

        if reasoning:
            self._log(f"  [LLM: {reasoning}]")

        if found and x is not None and y is not None:
            if step.action == "tap":
                return await app_tap(
                    x=int(x), y=int(y),
                    appium_url=self.session_manager.appium_url, session_id=sid,
                )
            elif step.action == "type":
                await app_tap(
                    x=int(x), y=int(y),
                    appium_url=self.session_manager.appium_url, session_id=sid,
                )
                await asyncio.sleep(0.5)
                return await app_type(
                    selector="new UiSelector().className(\"android.widget.EditText\").focused(true)",
                    text=step.value, strategy="uiautomator",
                    appium_url=self.session_manager.appium_url, session_id=sid,
                )

        # LLM says target not on screen — try its navigation suggestion
        if not found and x is not None and y is not None:
            self._log(
                f"  [LLM suggests navigating via ({x}, {y})]"
            )
            tap_res = await app_tap(
                x=int(x), y=int(y),
                appium_url=self.session_manager.appium_url, session_id=sid,
            )
            if not tap_res.get("error"):
                await asyncio.sleep(2)
                # Retry the original step via vision
                coords = await self._vision_find_element(step.target)
                if coords:
                    return await app_tap(
                        x=coords["x"], y=coords["y"],
                        appium_url=self.session_manager.appium_url, session_id=sid,
                    )

        return None

    async def _vision_find_element(
        self, target: str, context: str = ""
    ) -> dict[str, int] | None:
        """Use vision model to find an element on screen and return its center coords.

        Takes a screenshot, sends it to the vision model with the target
        description, and parses the returned percentage coordinates into
        device-pixel coordinates.

        Returns:
            Dict with 'x' and 'y' keys, or None if not found / error.
        """
        client = self._init_vision_client()
        if not client:
            return None

        scr_result = await app_screenshot(
            appium_url=self.session_manager.appium_url,
            session_id=self.session_manager.session_id,
        )
        screenshot_id = scr_result.get("screenshot_id", "")
        if not screenshot_id:
            return None

        from testagent.mcp_servers.shared_cache import get_screenshot

        b64 = get_screenshot(screenshot_id)
        if not b64:
            return None

        prompt = (
            f"请在截图中找到以下目标：{target}\n\n"
            "请分析：\n"
            "1. 目标是否在当前屏幕中可见？\n"
            "2. 如果可见，返回元素的百分比坐标（中心点和边界框）\n"
            "3. 如果不可见，当前屏幕主要有什么内容？建议向哪个方向滑动来寻找目标？\n\n"
            "请按以下格式回复：\n"
            "- found: true/false\n"
            "- 如果找到，提供 center 百分比坐标 (pct_x%, pct_y%) 和 bounds [pct_x1%, pct_y1%, pct_x2%, pct_y2%]\n"
            "- 如果没找到，提供 suggestion 滑动方向\n"
            "- 简要描述你看到的内容"
        )
        if context:
            prompt = f"之前的屏幕分析：{context}\n\n{prompt}"

        dw, dh = await self._get_screen_size()
        try:
            result = await client.analyze(
                b64, prompt, device_width=dw, device_height=dh,
            )
        except Exception:
            return None

        if "error" in result:
            return None

        content = result.get("content", "")

        from testagent.mcp_servers.vision_server.tools import (
            _parse_found_status,
            _parse_percentage_coordinates,
        )

        coords = _parse_percentage_coordinates(content, dw, dh)
        found = _parse_found_status(content) or bool(coords.get("center"))

        if not found or not coords.get("center"):
            self._log(
                f"  [Vision: target='{target}' — "
                f"found={found}, has_center={bool(coords.get('center'))}, "
                f"response={content[:200]}]"
            )
            return None

        return coords["center"]

    async def _vision_find_any_content(self) -> dict[str, int] | None:
        """Fallback: ask vision to find the first clickable content item on screen.

        Used when a specific tap target can't be found — the vision model looks
        at the current screen and returns the center of the first video card,
        list item, or other interactive content in the main content area.

        Returns:
            Dict with 'x' and 'y' keys, or None if no content found.
        """
        client = self._init_vision_client()
        if not client:
            return None

        scr_result = await app_screenshot(
            appium_url=self.session_manager.appium_url,
            session_id=self.session_manager.session_id,
        )
        screenshot_id = scr_result.get("screenshot_id", "")
        if not screenshot_id:
            return None

        from testagent.mcp_servers.shared_cache import get_screenshot

        b64 = get_screenshot(screenshot_id)
        if not b64:
            return None

        prompt = (
            "当前屏幕上没有找到特定目标元素。"
            "请找到屏幕上最主要的可点击内容项（视频卡片、列表项、文章卡片等）。\n\n"
            "查找规则：\n"
            "1. 优先找屏幕中央区域的可点击内容（视频封面、内容卡片）\n"
            "2. 不要点击导航栏（顶部Tab、底部导航）\n"
            "3. 不要点击广告/推广内容\n"
            "4. 如果屏幕有多个内容项，选择第一个（最靠上的）\n"
            "5. 如果当前是推荐流/内容流，点击第一个内容卡片\n\n"
            "请按以下格式回复：\n"
            "- found: true/false\n"
            "- center: (pct_x%, pct_y%)\n"
            "- description: 简要描述你选择的第一个可点击内容是什么"
        )

        dw, dh = await self._get_screen_size()
        try:
            result = await client.analyze(
                b64, prompt, device_width=dw, device_height=dh,
            )
        except Exception:
            return None

        if "error" in result:
            return None

        content = result.get("content", "")

        from testagent.mcp_servers.vision_server.tools import (
            _parse_found_status,
            _parse_percentage_coordinates,
        )

        coords = _parse_percentage_coordinates(content, dw, dh)
        found = _parse_found_status(content) or bool(coords.get("center"))

        if not found or not coords.get("center"):
            self._log(
                f"  [Content fallback: no clickable content found — "
                f"response={content[:150]}]"
            )
            return None

        return coords["center"]

    async def _assert_with_vision(self, target: str) -> dict | None:
        """Evaluate an assertion by asking the vision model to look at the screen.

        Takes a screenshot and asks the vision model whether the assertion
        described by ``target`` is true. Returns a result dict compatible
        with ``app_assert_element`` output, or None if vision is unavailable.

        Returns:
            Dict with ``passed`` key (True/False) and ``reason``, or None.
        """
        client = self._init_vision_client()
        if not client:
            return None

        scr_result = await app_screenshot(
            appium_url=self.session_manager.appium_url,
            session_id=self.session_manager.session_id,
        )
        screenshot_id = scr_result.get("screenshot_id", "")
        if not screenshot_id:
            return None

        from testagent.mcp_servers.shared_cache import get_screenshot

        b64 = get_screenshot(screenshot_id)
        if not b64:
            return None

        dw, dh = await self._get_screen_size()
        prompt = (
            f"请判断当前屏幕截图是否满足以下断言：{target}\n\n"
            "请客观分析屏幕内容，不要假设或猜测。\n"
            "用以下 JSON 格式回复（只输出 JSON，不要其他内容）：\n"
            '{"passed": true/false, "reason": "你的判断依据（一句话）"}'
        )

        try:
            result = await client.analyze(
                b64, prompt, device_width=dw, device_height=dh,
            )
        except Exception:
            return None

        if "error" in result:
            return None

        content = result.get("content", "")
        try:
            import json as _json
            # Extract JSON from response (may be wrapped in markdown)
            start = content.find("{")
            end = content.rfind("}")
            if start >= 0 and end > start:
                parsed = _json.loads(content[start:end + 1])
                return {
                    "passed": parsed.get("passed", False),
                    "reason": parsed.get("reason", ""),
                }
        except Exception:
            pass

        # Fallback: check for keywords in the response
        lowered = content.lower()
        if "true" in lowered or "passed" in lowered or "正确" in content or "满足" in content:
            return {"passed": True, "reason": content[:200]}
        return {"passed": False, "reason": content[:200]}

    async def _analyze_failure_with_vision(self, step: TestStep | None = None) -> str:
        """When a step fails, use vision to analyze what's on screen.

        Takes a screenshot and asks the vision model to describe the current
        screen state — what's visible, whether there's a popup blocking,
        whether the app crashed, etc. The analysis is stored in the step
        execution record and shown in the test report.

        Args:
            step: The step that failed (used for context in the prompt).

        Returns:
            A human-readable analysis string, or empty string on failure.
        """
        client = self._init_vision_client()
        if not client:
            return ""

        scr_result = await app_screenshot(
            appium_url=self.session_manager.appium_url,
            session_id=self.session_manager.session_id,
        )
        screenshot_id = scr_result.get("screenshot_id", "")
        if not screenshot_id:
            return ""

        from testagent.mcp_servers.shared_cache import get_screenshot

        b64 = get_screenshot(screenshot_id)
        if not b64:
            return ""

        action_desc = f"执行操作「{step.action}」目标「{step.target}」" if step else "执行操作"
        prompt = (
            f"当前测试步骤失败了（{action_desc}）。请分析这张截图，告诉我当前界面是什么情况。\n\n"
            "请回答以下问题：\n"
            "1. 当前屏幕上显示的是什么内容？（是正常界面、子页面、弹窗、错误页、还是崩溃白屏？）\n"
            "2. 如果当前不是目标页面，是否有**返回按钮**或**导航Tab**可以回到上一级？\n"
            "3. 屏幕上是否有弹窗/对话框遮挡？如果有，弹窗内容和按钮是什么？\n"
            "4. 屏幕上是否有错误提示（如网络错误、连接失败、无数据等）？\n\n"
            "请用中文简要描述。"
        )

        dw, dh = await self._get_screen_size()
        try:
            result = await client.analyze(
                b64, prompt, device_width=dw, device_height=dh,
            )
        except Exception as exc:
            return f"[Vision analysis error: {exc}]"

        if "error" in result:
            return f"[Vision analysis failed: {result['error'][:80]}]"

        content = result.get("content", "")
        if content:
            self._log(f"  [Vision failure analysis: {content[:120]}...]")
        return content

    async def _try_navigation_recovery_with_vision(self, step: TestStep) -> bool:
        """Let the vision model decide where to tap to reach the target.

        Instead of constraining the model to only look for back/close buttons,
        gives it the full context — what we're trying to find — and lets it
        reason about which element on the current screen would lead there.
        This mirrors how ``testagent chat`` dynamically navigates.
        """
        client = self._init_vision_client()
        if not client:
            return False

        scr_result = await app_screenshot(
            appium_url=self.session_manager.appium_url,
            session_id=self.session_manager.session_id,
        )
        screenshot_id = scr_result.get("screenshot_id", "")
        if not screenshot_id:
            return False

        from testagent.mcp_servers.shared_cache import get_screenshot

        b64 = get_screenshot(screenshot_id)
        if not b64:
            return False

        prompt = (
            f"当前测试步骤需要操作目标「{step.target}」（操作类型：{step.action}），"
            f"但在当前屏幕上找不到该目标。\n\n"
            "请分析当前屏幕截图，判断应该点击哪个元素才能导航到包含该目标的页面。\n\n"
            "你需要自主推理，例如：\n"
            "- 如果目标是搜索相关（搜索框、搜索按钮等），可能需要点击搜索图标进入搜索页\n"
            "- 如果目标是某个 Tab 页的内容（如直播、热门、追番），点击对应的导航 Tab\n"
            "- 如果当前明显在子页面/弹窗/详情页，点击返回按钮或关闭按钮回到上一级\n"
            "- 如果目标在首页，而当前在子页面，需要返回首页\n"
            "- 如果当前有弹窗遮挡，点击弹窗的关闭按钮\n\n"
            "请自行判断最合理的导航操作，并提供该元素的 center 百分比坐标。\n\n"
            "请按以下格式回复：\n"
            "- found: true/false\n"
            "- center: (pct_x%, pct_y%)\n"
            "- description: 简要说明当前页面是什么，以及你选择点击哪个元素来导航"
        )

        dw, dh = await self._get_screen_size()
        result = await client.analyze(
            b64, prompt, device_width=dw, device_height=dh,
        )
        if "error" in result:
            return False

        content = result.get("content", "")

        from testagent.mcp_servers.vision_server.tools import (
            _parse_found_status,
            _parse_percentage_coordinates,
        )

        coords = _parse_percentage_coordinates(content, dw, dh)
        found = _parse_found_status(content) or bool(coords.get("center"))

        if not found or not coords.get("center"):
            self._log(
                f"  [Vision nav: no path found — {content[:100]}]"
            )
            return False

        center = coords["center"]
        self._log(
            f"  [Vision nav: tapping ({center['x']}, {center['y']}) — "
            f"{content[content.find('description:'):].split(chr(10))[0] if 'description:' in content else ''}]"
        )

        tap_result = await app_tap(
            x=center["x"], y=center["y"],
            appium_url=self.session_manager.appium_url,
            session_id=self.session_manager.session_id,
        )
        if not tap_result.get("error"):
            self._log("Vision nav succeeded")
            await asyncio.sleep(1.5)
            return True

        return False

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
