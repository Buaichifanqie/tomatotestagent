from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from testagent.plan.models import (
    AbortPolicy,
    EvaluationOutput,
    EventLevel,
    EventLogEntry,
    EventType,
    EvidenceItem,
    ExecutionStatus,
    ExecutionVerdict,
    FailureType,
    OverallEvaluation,
    PlanConfig,
    PopupRule,
    Precondition,
    RetryPolicy,
    StepExecution,
    SuccessCondition,
    TCExecution,
    TestCase,
    TestStep,
    WaitCondition,
)


# ── Enums ──────────────────────────────────────────────────────────────────────


class TestExecutionStatus:
    def test_members(self) -> None:
        assert ExecutionStatus.PENDING.value == "PENDING"
        assert ExecutionStatus.RUNNING.value == "RUNNING"
        assert ExecutionStatus.EXECUTED.value == "EXECUTED"
        assert ExecutionStatus.FAILED.value == "FAILED"
        assert ExecutionStatus.BLOCKED.value == "BLOCKED"
        assert ExecutionStatus.ABORTED.value == "ABORTED"

    def test_all_members_covered(self) -> None:
        expected = {"PENDING", "RUNNING", "EXECUTED", "FAILED", "BLOCKED", "ABORTED"}
        assert {m.name for m in ExecutionStatus} == expected


class TestExecutionVerdict:
    def test_members(self) -> None:
        assert ExecutionVerdict.PASS.value == "PASS"
        assert ExecutionVerdict.FAIL.value == "FAIL"
        assert ExecutionVerdict.BLOCKED.value == "BLOCKED"
        assert ExecutionVerdict.NEED_REVIEW.value == "NEED_REVIEW"
        assert ExecutionVerdict.INCONCLUSIVE.value == "INCONCLUSIVE"
        assert ExecutionVerdict.PARTIAL.value == "PARTIAL"

    def test_all_members_covered(self) -> None:
        expected = {"PASS", "FAIL", "BLOCKED", "NEED_REVIEW", "INCONCLUSIVE", "PARTIAL", "SKIP"}
        assert {m.name for m in ExecutionVerdict} == expected


class TestFailureType:
    def test_members(self) -> None:
        assert FailureType.ACTION_FAILED.value == "ACTION_FAILED"
        assert FailureType.ASSERTION_FAILED.value == "ASSERTION_FAILED"
        assert FailureType.PRECONDITION_FAILED.value == "PRECONDITION_FAILED"
        assert FailureType.APP_CRASHED.value == "APP_CRASHED"
        assert FailureType.SESSION_LOST.value == "SESSION_LOST"
        assert FailureType.SCREENSHOT_FAILED.value == "SCREENSHOT_FAILED"
        assert FailureType.EVALUATION_UNCERTAIN.value == "EVALUATION_UNCERTAIN"
        assert FailureType.ENVIRONMENT_ERROR.value == "ENVIRONMENT_ERROR"

    def test_all_members_covered(self) -> None:
        expected = {
            "ACTION_FAILED",
            "ASSERTION_FAILED",
            "PRECONDITION_FAILED",
            "APP_CRASHED",
            "SESSION_LOST",
            "SCREENSHOT_FAILED",
            "EVALUATION_UNCERTAIN",
            "ENVIRONMENT_ERROR",
        }
        assert {m.name for m in FailureType} == expected


class TestEventLevel:
    def test_members(self) -> None:
        assert EventLevel.INFO.value == "INFO"
        assert EventLevel.WARNING.value == "WARNING"
        assert EventLevel.ERROR.value == "ERROR"

    def test_all_members_covered(self) -> None:
        expected = {"INFO", "WARNING", "ERROR"}
        assert {m.name for m in EventLevel} == expected


class TestEventType:
    def test_members(self) -> None:
        assert EventType.STEP_START.value == "STEP_START"
        assert EventType.STEP_END.value == "STEP_END"
        assert EventType.ACTION_EXECUTED.value == "ACTION_EXECUTED"
        assert EventType.ASSERTION_CHECKED.value == "ASSERTION_CHECKED"
        assert EventType.PRECONDITION_CHECK.value == "PRECONDITION_CHECK"
        assert EventType.POPUP_HANDLED.value == "POPUP_HANDLED"
        assert EventType.POPUP_SKIPPED.value == "POPUP_SKIPPED"
        assert EventType.APP_CRASHED.value == "APP_CRASHED"
        assert EventType.SESSION_RECOVERED.value == "SESSION_RECOVERED"
        assert EventType.SESSION_LOST.value == "SESSION_LOST"
        assert EventType.RETRYING.value == "RETRYING"
        assert EventType.TC_START.value == "TC_START"
        assert EventType.TC_END.value == "TC_END"
        assert EventType.ABORTED.value == "ABORTED"
        assert EventType.MANUAL_INTERVENTION.value == "MANUAL_INTERVENTION"

    def test_all_members_covered(self) -> None:
        expected = {
            "STEP_START",
            "STEP_END",
            "ACTION_EXECUTED",
            "ASSERTION_CHECKED",
            "PRECONDITION_CHECK",
            "POPUP_HANDLED",
            "POPUP_SKIPPED",
            "APP_CRASHED",
            "SESSION_RECOVERED",
            "SESSION_LOST",
            "RETRYING",
            "TC_START",
            "TC_END",
            "ABORTED",
            "MANUAL_INTERVENTION",
        }
        assert {m.name for m in EventType} == expected


# ── WaitCondition ──────────────────────────────────────────────────────────────


class TestWaitCondition:
    def test_minimal_creation(self) -> None:
        wc = WaitCondition(type="visibility", target="#submit")
        assert wc.type == "visibility"
        assert wc.target == "#submit"
        assert wc.timeout_ms == 5000

    def test_custom_timeout(self) -> None:
        wc = WaitCondition(type="exist", target=".modal", timeout_ms=3000)
        assert wc.timeout_ms == 3000


# ── SuccessCondition ───────────────────────────────────────────────────────────


class TestSuccessCondition:
    def test_defaults(self) -> None:
        sc = SuccessCondition(type="text_match", target="Hello")
        assert sc.type == "text_match"
        assert sc.target == "Hello"
        assert sc.value == ""

    def test_all_fields(self) -> None:
        sc = SuccessCondition(type="attribute_equals", target="data-status", value="loaded")
        assert sc.value == "loaded"


# ── TestStep ───────────────────────────────────────────────────────────────────


class TestTestStep:
    def test_minimal_creation(self) -> None:
        step = TestStep(step=1, action="click", target="#btn")
        assert step.step == 1
        assert step.action == "click"
        assert step.target == "#btn"
        assert step.value == ""
        assert step.timeout_ms == 10000
        assert step.poll_interval_ms == 500
        assert step.wait_after is None
        assert step.success_condition is None
        assert step.screenshot is True
        assert step.is_manual is False
        assert step.instruction == ""

    def test_with_wait_after_and_success_condition(self) -> None:
        wait = WaitCondition(type="visibility", target="#result")
        success = SuccessCondition(type="element_exists", target="#result")
        step = TestStep(
            step=2,
            action="type",
            target="#search",
            value="hello",
            timeout_ms=15000,
            poll_interval_ms=1000,
            wait_after=wait,
            success_condition=success,
            screenshot=False,
            is_manual=True,
            instruction="Type search query",
        )
        assert step.step == 2
        assert step.action == "type"
        assert step.wait_after is not None
        assert step.wait_after.target == "#result"
        assert step.success_condition is not None
        assert step.success_condition.target == "#result"
        assert step.screenshot is False
        assert step.is_manual is True
        assert step.instruction == "Type search query"


# ── Precondition ───────────────────────────────────────────────────────────────


class TestPrecondition:
    def test_defaults(self) -> None:
        pc = Precondition()
        assert pc.description == ""
        assert pc.setup == []
        assert pc.max_retries == 2

    def test_with_setup_steps(self) -> None:
        steps = [
            TestStep(step=1, action="click", target="#login"),
            TestStep(step=2, action="type", target="#username", value="user"),
        ]
        pc = Precondition(description="Login first", setup=steps, max_retries=3)
        assert pc.description == "Login first"
        assert len(pc.setup) == 2
        assert pc.max_retries == 3


# ── StepExecution ──────────────────────────────────────────────────────────────


class TestStepExecution:
    def test_minimal_creation(self) -> None:
        se = StepExecution(step=1, action="click", target="#btn", success=True)
        assert se.step == 1
        assert se.action == "click"
        assert se.target == "#btn"
        assert se.success is True
        assert se.failure_type is None
        assert se.error_message == ""
        assert se.duration_ms is None
        assert se.matched_count == 1
        assert se.screenshot_before == ""
        assert se.screenshot_after == ""
        assert se.page_source_before == ""
        assert se.page_source_after == ""

    def test_with_failure(self) -> None:
        se = StepExecution(
            step=1,
            action="click",
            target="#btn",
            success=False,
            failure_type=FailureType.ACTION_FAILED,
            error_message="Element not found",
            duration_ms=5000,
            matched_count=0,
            screenshot_before="s1.png",
            screenshot_after="s2.png",
            page_source_before="src1.html",
            page_source_after="src2.html",
        )
        assert se.success is False
        assert se.failure_type == FailureType.ACTION_FAILED
        assert se.error_message == "Element not found"
        assert se.duration_ms == 5000


# ── TCExecution ────────────────────────────────────────────────────────────────


class TestTCExecution:
    def test_default_pending_status(self) -> None:
        tc_ex = TCExecution()
        assert tc_ex.status == ExecutionStatus.PENDING
        assert tc_ex.verdict is None
        assert tc_ex.confidence is None
        assert tc_ex.failed_step is None
        assert tc_ex.failure_type is None
        assert tc_ex.error_message == ""
        assert tc_ex.evidence == []
        assert tc_ex.evidence_missing == []
        assert tc_ex.retries == 0
        assert tc_ex.steps == []
        assert tc_ex.duration_ms == 0
        assert tc_ex.reason == ""

    def test_with_steps_and_evidence(self) -> None:
        evidence = [EvidenceItem(type="screenshot", path="s1.png")]
        steps = [
            StepExecution(step=1, action="click", target="#btn", success=True),
        ]
        tc_ex = TCExecution(
            status=ExecutionStatus.EXECUTED,
            verdict=ExecutionVerdict.PASS,
            confidence=0.95,
            evidence=evidence,
            steps=steps,
            duration_ms=1500,
        )
        assert tc_ex.status == ExecutionStatus.EXECUTED
        assert tc_ex.verdict == ExecutionVerdict.PASS
        assert tc_ex.confidence == 0.95
        assert len(tc_ex.evidence) == 1
        assert len(tc_ex.steps) == 1
        assert tc_ex.duration_ms == 1500


# ── EvidenceItem ───────────────────────────────────────────────────────────────


class TestEvidenceItem:
    def test_creation(self) -> None:
        item = EvidenceItem(type="video", path="record.mp4")
        assert item.type == "video"
        assert item.path == "record.mp4"


# ── TestCase ───────────────────────────────────────────────────────────────────


class TestTestCase:
    def test_minimal_creation(self) -> None:
        steps = [TestStep(step=1, action="click", target="#btn")]
        tc = TestCase(id="TC-001", title="Login test", steps=steps)
        assert tc.id == "TC-001"
        assert tc.title == "Login test"
        assert tc.priority == "P1"
        assert tc.is_core is False
        assert tc.requirement_ids == []
        assert tc.precondition is None
        assert tc.teardown == []
        assert len(tc.steps) == 1
        assert tc.execution.status == ExecutionStatus.PENDING

    def test_with_all_fields(self) -> None:
        setup_steps = [TestStep(step=1, action="click", target="#login-btn")]
        teardown_steps = [TestStep(step=99, action="click", target="#logout")]
        main_steps = [
            TestStep(step=1, action="type", target="#user", value="admin"),
            TestStep(step=2, action="type", target="#pass", value="123"),
            TestStep(step=3, action="click", target="#submit"),
        ]
        precondition = Precondition(description="Login screen visible", setup=setup_steps)
        tc = TestCase(
            id="TC-002",
            title="Full login flow",
            priority="P0",
            is_core=True,
            requirement_ids=["REQ-001", "REQ-002"],
            precondition=precondition,
            teardown=teardown_steps,
            steps=main_steps,
        )
        assert tc.id == "TC-002"
        assert tc.priority == "P0"
        assert tc.is_core is True
        assert tc.requirement_ids == ["REQ-001", "REQ-002"]
        assert tc.precondition is not None
        assert tc.precondition.description == "Login screen visible"
        assert len(tc.precondition.setup) == 1
        assert len(tc.teardown) == 1
        assert len(tc.steps) == 3
        assert tc.execution.status == ExecutionStatus.PENDING

    def test_immutable_execution_default(self) -> None:
        """Each TestCase should get its own TCExecution instance, not share one."""
        tc1 = TestCase(id="TC-001", title="Test 1", steps=[TestStep(step=1, action="click", target="#a")])
        tc2 = TestCase(id="TC-002", title="Test 2", steps=[TestStep(step=1, action="click", target="#b")])
        tc1.execution.status = ExecutionStatus.RUNNING
        assert tc2.execution.status == ExecutionStatus.PENDING


# ── EvaluationOutput ───────────────────────────────────────────────────────────


class TestEvaluationOutput:
    def test_creation(self) -> None:
        ev = EvaluationOutput(
            verdict=ExecutionVerdict.PASS,
            confidence=0.92,
            reason="All assertions passed",
            evidence=[EvidenceItem(type="screenshot", path="final.png")],
            evidence_missing=["step_2_screenshot"],
            evaluation_notes="Minor timing issue",
            failure_type=None,
        )
        assert ev.verdict == ExecutionVerdict.PASS
        assert ev.confidence == 0.92
        assert ev.reason == "All assertions passed"
        assert len(ev.evidence) == 1
        assert "step_2_screenshot" in ev.evidence_missing

    def test_defaults(self) -> None:
        ev = EvaluationOutput(
            verdict=ExecutionVerdict.FAIL,
            confidence=0.5,
            reason="Assertion failed",
        )
        assert ev.evidence == []
        assert ev.evidence_missing == []
        assert ev.evaluation_notes == ""


# ── OverallEvaluation ──────────────────────────────────────────────────────────


class TestOverallEvaluation:
    def test_pass_rate_property(self) -> None:
        oe = OverallEvaluation(
            verdict=ExecutionVerdict.PASS,
            total_count=10,
            passed_count=7,
        )
        assert oe.pass_rate == "7/10"

    def test_core_pass_rate_with_values(self) -> None:
        oe = OverallEvaluation(
            verdict=ExecutionVerdict.PASS,
            total_count=5,
            passed_count=4,
            core_total=3,
            core_passed=2,
        )
        assert oe.core_pass_rate == "2/3"

    def test_core_pass_rate_none(self) -> None:
        """When core_total is 0, core_pass_rate should return 'N/A'."""
        oe = OverallEvaluation(
            verdict=ExecutionVerdict.PASS,
            total_count=5,
            passed_count=4,
            core_total=0,
            core_passed=0,
        )
        assert oe.core_pass_rate == "N/A"

    def test_review_recommendations_default(self) -> None:
        oe = OverallEvaluation(
            verdict=ExecutionVerdict.PASS,
            total_count=0,
            passed_count=0,
        )
        assert oe.review_recommendations == []


# ── EventLogEntry ──────────────────────────────────────────────────────────────


class TestEventLogEntry:
    def test_minimal_creation(self) -> None:
        entry = EventLogEntry(
            time=datetime(2025, 1, 1, 0, 0, 0),
            level=EventLevel.INFO,
            event_type=EventType.STEP_START,
        )
        assert entry.time == datetime(2025, 1, 1, 0, 0, 0)
        assert entry.level == EventLevel.INFO
        assert entry.event_type == EventType.STEP_START
        assert entry.step is None
        assert entry.tc_id == ""
        assert entry.message == ""
        assert entry.locator == ""
        assert entry.matched_count is None
        assert entry.failure_type is None
        assert entry.duration_ms is None

    def test_full_creation(self) -> None:
        entry = EventLogEntry(
            time=datetime(2025, 1, 1, 0, 0, 5),
            level=EventLevel.ERROR,
            event_type=EventType.APP_CRASHED,
            step=1,
            tc_id="TC-001",
            message="Element not found",
            locator="#btn",
            matched_count=0,
            failure_type=FailureType.ACTION_FAILED,
            duration_ms=5000,
        )
        assert entry.step == 1
        assert entry.tc_id == "TC-001"
        assert entry.message == "Element not found"
        assert entry.matched_count == 0
        assert entry.failure_type == FailureType.ACTION_FAILED


# ── PopupRule ──────────────────────────────────────────────────────────────────


class TestPopupRule:
    def test_minimal_creation(self) -> None:
        rule = PopupRule(name="allow", target_text=["OK"], action="click")
        assert rule.name == "allow"
        assert rule.target_text == ["OK"]
        assert rule.action == "click"
        assert rule.button_text == ""

    def test_with_button_text(self) -> None:
        rule = PopupRule(
            name="dismiss",
            target_text=["Update", "Upgrade"],
            action="click",
            button_text="Later",
        )
        assert rule.button_text == "Later"


# ── RetryPolicy ────────────────────────────────────────────────────────────────


class TestRetryPolicy:
    def test_defaults(self) -> None:
        rp = RetryPolicy()
        assert rp.step == 1
        assert rp.test_case == 1
        assert rp.session == 3
        assert rp.app_crash == 1

    def test_custom_values(self) -> None:
        rp = RetryPolicy(step=3, test_case=2, session=5, app_crash=3)
        assert rp.step == 3
        assert rp.test_case == 2
        assert rp.session == 5


# ── AbortPolicy ────────────────────────────────────────────────────────────────


class TestAbortPolicy:
    def test_defaults(self) -> None:
        ap = AbortPolicy()
        assert ap.max_consecutive_blocked == 3
        assert ap.max_session_recreate == 2
        assert ap.max_total_duration_ms == 18_000_000
        assert ap.abort_on == ["SESSION_LOST", "ENVIRONMENT_ERROR"]

    def test_custom_values(self) -> None:
        ap = AbortPolicy(
            max_consecutive_blocked=5,
            max_total_duration_ms=3_600_000,
            abort_on=["SESSION_LOST"],
        )
        assert ap.max_consecutive_blocked == 5
        assert ap.abort_on == ["SESSION_LOST"]


# ── PlanConfig ─────────────────────────────────────────────────────────────────


class TestPlanConfig:
    def test_defaults(self) -> None:
        config = PlanConfig()
        assert config.name == ""
        assert config.app_package == ""
        assert config.app_activity == ""
        assert config.output_dir == ""
        assert config.auto_yes is False
        assert config.retry.step == 1
        assert config.abort_policy.max_consecutive_blocked == 3
        assert config.popup_rules == []
        assert config.max_workers == 1

    def test_full_configuration(self) -> None:
        rules = [
            PopupRule(name="grant", target_text=["Allow"], action="click", button_text="Allow"),
        ]
        config = PlanConfig(
            name="My Plan",
            app_package="com.example.app",
            app_activity=".MainActivity",
            output_dir="./results",
            auto_yes=True,
            retry=RetryPolicy(step=2, test_case=3),
            abort_policy=AbortPolicy(max_consecutive_blocked=5),
            popup_rules=rules,
            max_workers=4,
        )
        assert config.name == "My Plan"
        assert config.auto_yes is True
        assert config.retry.step == 2
        assert config.max_workers == 4
        assert len(config.popup_rules) == 1


# ── Validation Tests ─────────────────────────────────────────────────────────


class TestValidation:
    def test_rejects_negative_timeout(self) -> None:
        with pytest.raises(ValidationError):
            TestStep(step=1, action="tap", target="x", timeout_ms=-1)

    def test_rejects_out_of_range_confidence(self) -> None:
        with pytest.raises(ValidationError):
            EvaluationOutput(verdict=ExecutionVerdict.PASS, confidence=2.5, reason="bad")

    def test_rejects_string_for_step(self) -> None:
        with pytest.raises(ValidationError):
            TestStep(step="not_an_int", action="tap", target="x")  # type: ignore[arg-type]

    def test_rejects_missing_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            TestStep()  # type: ignore[call-arg]

    def test_rejects_event_log_entry_missing_required(self) -> None:
        with pytest.raises(ValidationError):
            EventLogEntry()  # type: ignore[call-arg]
