from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────────────────────────


class ExecutionStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    ABORTED = "ABORTED"


class ExecutionVerdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    BLOCKED = "BLOCKED"
    NEED_REVIEW = "NEED_REVIEW"
    INCONCLUSIVE = "INCONCLUSIVE"
    PARTIAL = "PARTIAL"
    SKIP = "SKIP"


class FailureType(str, Enum):
    ACTION_FAILED = "ACTION_FAILED"
    ASSERTION_FAILED = "ASSERTION_FAILED"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    APP_CRASHED = "APP_CRASHED"
    SESSION_LOST = "SESSION_LOST"
    SCREENSHOT_FAILED = "SCREENSHOT_FAILED"
    EVALUATION_UNCERTAIN = "EVALUATION_UNCERTAIN"
    ENVIRONMENT_ERROR = "ENVIRONMENT_ERROR"


class EventLevel(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class EventType(str, Enum):
    STEP_START = "STEP_START"
    STEP_END = "STEP_END"
    ACTION_EXECUTED = "ACTION_EXECUTED"
    ASSERTION_CHECKED = "ASSERTION_CHECKED"
    PRECONDITION_CHECK = "PRECONDITION_CHECK"
    POPUP_HANDLED = "POPUP_HANDLED"
    POPUP_SKIPPED = "POPUP_SKIPPED"
    APP_CRASHED = "APP_CRASHED"
    SESSION_RECOVERED = "SESSION_RECOVERED"
    SESSION_LOST = "SESSION_LOST"
    RETRYING = "RETRYING"
    TC_START = "TC_START"
    TC_END = "TC_END"
    ABORTED = "ABORTED"
    MANUAL_INTERVENTION = "MANUAL_INTERVENTION"


# ── Pydantic Models ────────────────────────────────────────────────────────────


class WaitCondition(BaseModel):
    type: str
    target: str
    timeout_ms: int = 5000


class SuccessCondition(BaseModel):
    type: str = "element_exists"
    target: str = ""
    value: str = ""


class TestStep(BaseModel):
    __test__ = False
    step: int
    action: str
    target: str
    value: str = ""
    expected: str = ""
    timeout_ms: int = Field(default=10000, ge=0)
    poll_interval_ms: int = Field(default=500, ge=0)
    wait_after: WaitCondition | None = None
    success_condition: SuccessCondition | None = None
    screenshot: bool = True
    is_manual: bool = False
    instruction: str = ""
    tap_first: str = ""  # 先点击此区域让隐藏控件浮现，再操作 target


class Precondition(BaseModel):
    description: str = ""
    setup: list[TestStep] = Field(default_factory=list)
    max_retries: int = 2


class StepExecution(BaseModel):
    step: int
    action: str
    target: str
    success: bool
    failure_type: FailureType | None = None
    error_message: str = ""
    duration_ms: int | None = None
    matched_count: int = 1
    screenshot_before: str = ""
    screenshot_after: str = ""
    page_source_before: str = ""
    page_source_after: str = ""
    vision_analysis: str = ""  # Multimodal model's analysis of the screen on failure
    source: str = ""  # 来源标识："" 表示 LLM 视觉识别，"cache:TC-xxx/stepN" 表示缓存命中
    warning: str = ""  # Assert warning message (when assert downgraded to warning)
    coords: dict = Field(default_factory=dict)  # Action coordinates for marker drawing {x, y, ...}


class EvidenceItem(BaseModel):
    type: str
    path: str


class TCExecution(BaseModel):
    status: ExecutionStatus = ExecutionStatus.PENDING
    verdict: ExecutionVerdict | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    failed_step: int | None = None
    failure_type: FailureType | None = None
    error_message: str = ""
    evidence: list[EvidenceItem] = Field(default_factory=list)
    evidence_missing: list[str] = Field(default_factory=list)
    retries: int = 0
    steps: list[StepExecution] = Field(default_factory=list)
    duration_ms: int = 0
    reason: str = ""
    previous_attempts: list[dict[str, object]] = Field(default_factory=list)
    assert_warnings: list[str] = Field(default_factory=list)
    cross_source_results: list[dict[str, object]] = Field(default_factory=list)

    # CaseJudgeAgent output fields
    failure_category: str = ""  # BUG / ENVIRONMENT / TEST_ISSUE / FLAKY / NONE
    failure_root_cause: str = ""
    judge_evidence: list[str] = Field(default_factory=list)
    judge_confidence: float = 0.0
    judge_reasoning: str = ""


class TestCase(BaseModel):
    __test__ = False
    id: str
    title: str
    priority: str = "P1"
    is_core: bool = False
    feature_id: str = ""
    coverage_dimension: str = ""
    scenario_question: str = ""
    prerequisites: list[str] = Field(default_factory=list)
    expected_outcome: str = ""
    requirement_ids: list[str] = Field(default_factory=list)
    required_state: list[str] = Field(default_factory=list)
    precondition: Precondition | None = None
    teardown: list[TestStep] = Field(default_factory=list)
    steps: list[TestStep] = Field(default_factory=list)
    execution: TCExecution = Field(default_factory=TCExecution)
    setup: list[dict[str, object]] = Field(default_factory=list)
    assertions: list[dict[str, object]] = Field(default_factory=list)


class EvaluationOutput(BaseModel):
    verdict: ExecutionVerdict
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    evidence_missing: list[str] = Field(default_factory=list)
    evaluation_notes: str = ""
    failure_type: FailureType | None = None


class OverallEvaluation(BaseModel):
    verdict: ExecutionVerdict
    total_count: int
    passed_count: int
    core_total: int = 0
    core_passed: int = 0
    need_review_count: int = 0
    blocked_count: int = 0
    summary: str = ""
    review_recommendations: list[str] = Field(default_factory=list)

    @property
    def pass_rate(self) -> str:
        return f"{self.passed_count}/{self.total_count}"

    @property
    def core_pass_rate(self) -> str:
        if self.core_total == 0:
            return "N/A"
        return f"{self.core_passed}/{self.core_total}"


class EventLogEntry(BaseModel):
    time: datetime
    level: EventLevel
    event_type: EventType
    step: int | None = None
    tc_id: str = ""
    message: str = ""
    locator: str = ""
    matched_count: int | None = None
    failure_type: FailureType | None = None
    duration_ms: int | None = None


class PopupRule(BaseModel):
    name: str
    target_text: list[str]
    action: str
    button_text: str = ""


class RetryPolicy(BaseModel):
    step: int = 1
    test_case: int = 1
    session: int = 3
    app_crash: int = 1


class AbortPolicy(BaseModel):
    max_consecutive_blocked: int = 3
    max_session_recreate: int = 2
    max_total_duration_ms: int = 18_000_000
    abort_on: list[str] = Field(default_factory=lambda: ["SESSION_LOST", "ENVIRONMENT_ERROR"])


class PlanConfig(BaseModel):
    name: str = ""
    platform: str = "android"
    app_package: str = ""
    app_id: str = ""
    app_activity: str = ""
    output_dir: str = ""
    auto_yes: bool = False
    device_udid: str = ""
    appium_url: str = "http://localhost:4723"
    system_port: int = 8200
    wda_local_port: int = 8100
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    abort_policy: AbortPolicy = Field(default_factory=AbortPolicy)
    popup_rules: list[PopupRule] = Field(default_factory=list)
    max_workers: int = 1
    cache_enabled: bool = True
    cache_verify_after_tap: bool = False

    def get_effective_system_port(self) -> int:
        """Return the system port to use for this device.

        When ``system_port`` is the default 8200 and a ``device_udid`` is set,
        auto-assign a unique port (8200-8299) based on the device UDID hash.
        This allows multiple devices to share one Appium server without
        systemPort conflicts.
        """
        if self.system_port != 8200 or not self.device_udid:
            return self.system_port
        return 8200 + (sum(ord(c) for c in self.device_udid) % 100)
