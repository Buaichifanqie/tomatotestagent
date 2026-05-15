from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import ClassVar

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from testagent.models import (
    DEFECT_CATEGORIES,
    DEFECT_SEVERITIES,
    DEFECT_STATUSES,
    ISOLATION_LEVELS,
    PLAN_STATUSES,
    RESULT_STATUSES,
    SESSION_STATUSES,
    STRATEGY_TYPES,
    TASK_STATUSES,
    TASK_TYPES,
    TRIGGER_TYPES,
    Base,
    Defect,
    MCPConfig,
    SkillDefinition,
    TestPlan,
    TestResult,
    TestSession,
    TestTask,
)


@pytest.fixture()
def engine():
    eng = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture()
def session(engine):
    with Session(engine) as sess:
        yield sess


class TestBaseModel:
    def test_id_auto_generated_uuid(self, session: Session) -> None:
        s = TestSession(name="test", status="pending", trigger_type="manual")
        session.add(s)
        session.flush()
        parsed = uuid.UUID(s.id, version=4)
        assert str(parsed) == s.id

    def test_created_at_auto_set(self, session: Session) -> None:
        before = datetime.now(UTC)
        s = TestSession(name="test", status="pending", trigger_type="manual")
        session.add(s)
        session.flush()
        after = datetime.now(UTC)
        assert before <= s.created_at <= after

    def test_id_is_primary_key(self) -> None:
        mapper = inspect(TestSession)
        pk_cols = [c.name for c in mapper.primary_key]
        assert "id" in pk_cols


class TestTestSession:
    def test_instantiation_with_defaults(self, session: Session) -> None:
        s = TestSession(name="my-session", status="pending", trigger_type="manual")
        session.add(s)
        session.flush()
        assert s.name == "my-session"
        assert s.status == "pending"
        assert s.trigger_type == "manual"
        assert s.input_context is None
        assert s.completed_at is None

    def test_full_instantiation(self, session: Session) -> None:
        ctx = {"url": "https://example.com", "env": "staging"}
        now = datetime.now(UTC)
        s = TestSession(
            name="full-session",
            status="completed",
            trigger_type="ci_push",
            input_context=ctx,
            completed_at=now,
        )
        session.add(s)
        session.flush()
        assert s.input_context == ctx
        assert s.completed_at == now

    def test_session_statuses_constant(self) -> None:
        assert SESSION_STATUSES == ("pending", "planning", "executing", "analyzing", "completed", "failed")

    def test_trigger_types_constant(self) -> None:
        assert TRIGGER_TYPES == ("manual", "ci_push", "ci_pr", "scheduled")

    def test_session_relationship_with_plans(self, session: Session) -> None:
        s = TestSession(name="rel-test", status="planning", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": []},
            status="pending",
        )
        session.add(p)
        session.flush()
        assert len(s.plans) == 1
        assert s.plans[0].strategy_type == "smoke"

    def test_session_status_valid_values(self) -> None:
        valid = set(SESSION_STATUSES)
        assert "pending" in valid
        assert "planning" in valid
        assert "executing" in valid
        assert "analyzing" in valid
        assert "completed" in valid
        assert "failed" in valid


class TestTestPlan:
    def test_instantiation_with_defaults(self, session: Session) -> None:
        s = TestSession(name="plan-test", status="pending", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="regression",
            plan_json={"steps": ["step1"]},
        )
        session.add(p)
        session.flush()
        assert p.status == "pending"
        assert p.total_tasks == 0
        assert p.completed_tasks == 0
        assert p.skill_ref is None

    def test_full_instantiation(self, session: Session) -> None:
        s = TestSession(name="plan-full", status="pending", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": []},
            skill_ref="api_smoke_test",
            status="in_progress",
            total_tasks=5,
            completed_tasks=2,
        )
        session.add(p)
        session.flush()
        assert p.skill_ref == "api_smoke_test"
        assert p.status == "in_progress"
        assert p.total_tasks == 5
        assert p.completed_tasks == 2

    def test_plan_statuses_constant(self) -> None:
        assert PLAN_STATUSES == ("pending", "in_progress", "completed", "failed")

    def test_strategy_types_constant(self) -> None:
        assert STRATEGY_TYPES == ("smoke", "regression", "exploratory", "incremental")

    def test_plan_relationship_with_tasks(self, session: Session) -> None:
        s = TestSession(name="task-rel", status="pending", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": []},
        )
        session.add(p)
        session.flush()
        t = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "https://api.example.com"},
        )
        session.add(t)
        session.flush()
        assert len(p.tasks) == 1
        assert p.tasks[0].task_type == "api_test"


class TestTestTask:
    def test_instantiation_with_defaults(self, session: Session) -> None:
        s = TestSession(name="task-test", status="pending", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": []},
        )
        session.add(p)
        session.flush()
        t = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"method": "GET", "url": "/health"},
        )
        session.add(t)
        session.flush()
        assert t.isolation_level == "docker"
        assert t.priority == 0
        assert t.status == "queued"
        assert t.retry_count == 0
        assert t.depends_on is None
        assert t.started_at is None
        assert t.completed_at is None
        assert t.skill_ref is None

    def test_full_instantiation(self, session: Session) -> None:
        s = TestSession(name="task-full", status="executing", trigger_type="ci_pr")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="regression",
            plan_json={"steps": []},
            status="in_progress",
        )
        session.add(p)
        session.flush()
        now = datetime.now(UTC)
        t = TestTask(
            plan_id=p.id,
            task_type="web_test",
            skill_ref="web_smoke_test",
            task_config={"url": "https://staging.example.com"},
            isolation_level="docker",
            priority=10,
            status="running",
            retry_count=1,
            depends_on=None,
            started_at=now,
            completed_at=None,
        )
        session.add(t)
        session.flush()
        assert t.task_type == "web_test"
        assert t.skill_ref == "web_smoke_test"
        assert t.priority == 10
        assert t.status == "running"

    def test_task_statuses_constant(self) -> None:
        assert TASK_STATUSES == ("queued", "running", "passed", "failed", "flaky", "skipped", "retrying")

    def test_task_types_constant(self) -> None:
        assert TASK_TYPES == ("api_test", "web_test", "app_test")

    def test_isolation_levels_constant(self) -> None:
        assert ISOLATION_LEVELS == ("docker", "microvm", "local")

    def test_task_self_referential_depends_on(self, session: Session) -> None:
        s = TestSession(name="dep-test", status="pending", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="regression",
            plan_json={"steps": []},
        )
        session.add(p)
        session.flush()
        t1 = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/setup"},
        )
        session.add(t1)
        session.flush()
        t2 = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/verify"},
            depends_on=t1.id,
        )
        session.add(t2)
        session.flush()
        assert t2.depends_on == t1.id


class TestTestResult:
    def test_instantiation_with_defaults(self, session: Session) -> None:
        s = TestSession(name="result-test", status="executing", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": []},
        )
        session.add(p)
        session.flush()
        t = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/health"},
        )
        session.add(t)
        session.flush()
        r = TestResult(
            task_id=t.id,
            status="passed",
        )
        session.add(r)
        session.flush()
        assert r.duration_ms is None
        assert r.assertion_results is None
        assert r.logs is None
        assert r.screenshot_url is None
        assert r.video_url is None
        assert r.artifacts is None

    def test_full_instantiation(self, session: Session) -> None:
        s = TestSession(name="result-full", status="executing", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": []},
        )
        session.add(p)
        session.flush()
        t = TestTask(
            plan_id=p.id,
            task_type="web_test",
            task_config={"url": "/page"},
        )
        session.add(t)
        session.flush()
        r = TestResult(
            task_id=t.id,
            status="failed",
            duration_ms=1234.5,
            assertion_results={"total": 5, "passed": 3, "failed": 2},
            logs="Error: timeout",
            screenshot_url="https://storage.example.com/screenshot.png",
            video_url="https://storage.example.com/video.mp4",
            artifacts={"har": "https://storage.example.com/trace.har"},
        )
        session.add(r)
        session.flush()
        assert r.status == "failed"
        assert r.duration_ms == 1234.5
        assert r.assertion_results["total"] == 5
        assert r.logs == "Error: timeout"

    def test_result_statuses_constant(self) -> None:
        assert RESULT_STATUSES == ("passed", "failed", "error", "flaky")

    def test_task_result_relationship(self, session: Session) -> None:
        s = TestSession(name="rel-result", status="executing", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": []},
        )
        session.add(p)
        session.flush()
        t = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/health"},
        )
        session.add(t)
        session.flush()
        r = TestResult(
            task_id=t.id,
            status="passed",
        )
        session.add(r)
        session.flush()
        assert t.result is not None
        assert t.result.status == "passed"

    def test_task_id_unique_constraint(self, session: Session) -> None:
        s = TestSession(name="unique-test", status="executing", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": []},
        )
        session.add(p)
        session.flush()
        t = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/health"},
        )
        session.add(t)
        session.flush()
        r1 = TestResult(task_id=t.id, status="passed")
        session.add(r1)
        session.flush()
        r2 = TestResult(task_id=t.id, status="failed")
        session.add(r2)
        with pytest.raises(IntegrityError):
            session.flush()


class TestDefect:
    def test_instantiation_with_defaults(self, session: Session) -> None:
        s = TestSession(name="defect-test", status="analyzing", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": []},
        )
        session.add(p)
        session.flush()
        t = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/test"},
        )
        session.add(t)
        session.flush()
        r = TestResult(task_id=t.id, status="failed")
        session.add(r)
        session.flush()
        d = Defect(
            result_id=r.id,
            severity="major",
            category="bug",
            title="API returns 500 on /login",
        )
        session.add(d)
        session.flush()
        assert d.status == "open"
        assert d.description is None
        assert d.reproduction_steps is None
        assert d.jira_key is None
        assert d.root_cause is None

    def test_full_instantiation(self, session: Session) -> None:
        s = TestSession(name="defect-full", status="analyzing", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="regression",
            plan_json={"steps": []},
        )
        session.add(p)
        session.flush()
        t = TestTask(
            plan_id=p.id,
            task_type="web_test",
            task_config={"url": "/page"},
        )
        session.add(t)
        session.flush()
        r = TestResult(task_id=t.id, status="failed")
        session.add(r)
        session.flush()
        d = Defect(
            result_id=r.id,
            severity="critical",
            category="environment",
            title="DB connection timeout",
            description="Database becomes unreachable under load",
            reproduction_steps="1. Send 1000 requests\n2. Observe DB timeout",
            jira_key="PROJ-1234",
            status="confirmed",
            root_cause={"component": "db-pool", "threshold_exceeded": True},
        )
        session.add(d)
        session.flush()
        assert d.severity == "critical"
        assert d.category == "environment"
        assert d.jira_key == "PROJ-1234"
        assert d.status == "confirmed"
        assert d.root_cause["component"] == "db-pool"

    def test_defect_severities_constant(self) -> None:
        assert DEFECT_SEVERITIES == ("critical", "major", "minor", "trivial")

    def test_defect_categories_constant(self) -> None:
        assert DEFECT_CATEGORIES == ("bug", "flaky", "environment", "configuration")

    def test_defect_statuses_constant(self) -> None:
        assert DEFECT_STATUSES == ("open", "confirmed", "resolved", "closed")

    def test_jira_key_unique_constraint(self, session: Session) -> None:
        s = TestSession(name="jira-unique", status="analyzing", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": []},
        )
        session.add(p)
        session.flush()
        t1 = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/t1"},
        )
        session.add(t1)
        session.flush()
        t2 = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/t2"},
        )
        session.add(t2)
        session.flush()
        r1 = TestResult(task_id=t1.id, status="failed")
        session.add(r1)
        session.flush()
        r2 = TestResult(task_id=t2.id, status="failed")
        session.add(r2)
        session.flush()
        d1 = Defect(
            result_id=r1.id,
            severity="major",
            category="bug",
            title="Defect 1",
            jira_key="PROJ-9999",
        )
        session.add(d1)
        session.flush()
        d2 = Defect(
            result_id=r2.id,
            severity="minor",
            category="bug",
            title="Defect 2",
            jira_key="PROJ-9999",
        )
        session.add(d2)
        with pytest.raises(IntegrityError):
            session.flush()


class TestSkillDefinition:
    def test_instantiation_with_defaults(self, session: Session) -> None:
        sk = SkillDefinition(
            name="api_smoke_test",
            version="1.0.0",
            description="API smoke test skill",
        )
        session.add(sk)
        session.flush()
        assert sk.trigger_pattern is None
        assert sk.required_mcp_servers is None
        assert sk.required_rag_collections is None
        assert sk.body is None
        assert sk.tags is None
        assert sk.updated_at is None

    def test_full_instantiation(self, session: Session) -> None:
        sk = SkillDefinition(
            name="web_smoke_test",
            version="2.0.0",
            description="Web smoke test skill",
            trigger_pattern="web.*smoke",
            required_mcp_servers=["playwright_server"],
            required_rag_collections=["req_docs", "locator_library"],
            body="## Objective\nVerify web page loads correctly.",
            tags=["web", "smoke", "mvp"],
            updated_at=datetime.now(UTC),
        )
        session.add(sk)
        session.flush()
        assert sk.trigger_pattern == "web.*smoke"
        assert sk.required_mcp_servers == ["playwright_server"]
        assert sk.tags == ["web", "smoke", "mvp"]

    def test_name_version_unique_constraint(self, session: Session) -> None:
        sk1 = SkillDefinition(
            name="api_smoke_test",
            version="1.0.0",
            description="First version",
        )
        session.add(sk1)
        session.flush()
        sk2 = SkillDefinition(
            name="api_smoke_test",
            version="1.0.0",
            description="Duplicate version",
        )
        session.add(sk2)
        with pytest.raises(IntegrityError):
            session.flush()

    def test_same_name_different_version(self, session: Session) -> None:
        sk1 = SkillDefinition(
            name="api_smoke_test",
            version="1.0.0",
            description="First version",
        )
        session.add(sk1)
        session.flush()
        sk2 = SkillDefinition(
            name="api_smoke_test",
            version="2.0.0",
            description="Second version",
        )
        session.add(sk2)
        session.flush()
        assert sk1.id != sk2.id


class TestMCPConfig:
    def test_instantiation_with_defaults(self, session: Session) -> None:
        mc = MCPConfig(
            server_name="playwright-server",
            command="npx",
        )
        session.add(mc)
        session.flush()
        assert mc.session_id is None
        assert mc.args is None
        assert mc.env is None
        assert mc.enabled is True

    def test_full_instantiation(self, session: Session) -> None:
        s = TestSession(name="mcp-test", status="planning", trigger_type="manual")
        session.add(s)
        session.flush()
        mc = MCPConfig(
            session_id=s.id,
            server_name="api-server",
            command="python",
            args={"script": "mcp_api_server.py", "port": 8080},
            env={"API_BASE_URL": "https://staging.example.com"},
            enabled=True,
        )
        session.add(mc)
        session.flush()
        assert mc.session_id == s.id
        assert mc.args["script"] == "mcp_api_server.py"
        assert mc.env["API_BASE_URL"] == "https://staging.example.com"

    def test_server_name_unique_constraint(self, session: Session) -> None:
        mc1 = MCPConfig(
            server_name="duplicate-server",
            command="cmd1",
        )
        session.add(mc1)
        session.flush()
        mc2 = MCPConfig(
            server_name="duplicate-server",
            command="cmd2",
        )
        session.add(mc2)
        with pytest.raises(IntegrityError):
            session.flush()


class TestSessionStateMachine:
    VALID_TRANSITIONS: ClassVar[dict[str, list[str]]] = {
        "pending": ["planning"],
        "planning": ["executing"],
        "executing": ["analyzing"],
        "analyzing": ["completed", "failed"],
        "completed": [],
        "failed": [],
    }

    def test_valid_transitions(self) -> None:
        for src, targets in self.VALID_TRANSITIONS.items():
            assert src in SESSION_STATUSES
            for tgt in targets:
                assert tgt in SESSION_STATUSES

    def test_pending_is_initial_state(self, session: Session) -> None:
        s = TestSession(name="init", status="pending", trigger_type="manual")
        assert s.status == "pending"

    def test_no_transition_from_terminal_states(self) -> None:
        assert self.VALID_TRANSITIONS["completed"] == []
        assert self.VALID_TRANSITIONS["failed"] == []

    def test_all_session_statuses_reachable(self) -> None:
        reachable: set[str] = set()
        frontier = {"pending"}
        while frontier:
            state = frontier.pop()
            if state in reachable:
                continue
            reachable.add(state)
            for nxt in self.VALID_TRANSITIONS.get(state, []):
                frontier.add(nxt)
        assert reachable == set(SESSION_STATUSES)


class TestTaskStatusValues:
    @pytest.mark.parametrize("status", TASK_STATUSES)
    def test_all_task_statuses_are_valid(self, status: str, session: Session) -> None:
        s = TestSession(name=f"task-{status}", status="executing", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": []},
        )
        session.add(p)
        session.flush()
        t = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/test"},
            status=status,
        )
        session.add(t)
        session.flush()
        assert t.status == status


class TestDefectCategoryAndSeverity:
    @pytest.mark.parametrize("severity", DEFECT_SEVERITIES)
    def test_all_severities_are_valid(self, severity: str, session: Session) -> None:
        s = TestSession(name=f"sev-{severity}", status="analyzing", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": []},
        )
        session.add(p)
        session.flush()
        t = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/test"},
        )
        session.add(t)
        session.flush()
        r = TestResult(task_id=t.id, status="failed")
        session.add(r)
        session.flush()
        d = Defect(
            result_id=r.id,
            severity=severity,
            category="bug",
            title=f"Test defect {severity}",
        )
        session.add(d)
        session.flush()
        assert d.severity == severity

    @pytest.mark.parametrize("category", DEFECT_CATEGORIES)
    def test_all_categories_are_valid(self, category: str, session: Session) -> None:
        s = TestSession(name=f"cat-{category}", status="analyzing", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": []},
        )
        session.add(p)
        session.flush()
        t = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/test"},
        )
        session.add(t)
        session.flush()
        r = TestResult(task_id=t.id, status="failed")
        session.add(r)
        session.flush()
        d = Defect(
            result_id=r.id,
            severity="major",
            category=category,
            title=f"Test defect {category}",
        )
        session.add(d)
        session.flush()
        assert d.category == category


class TestCascadingDeletes:
    def test_session_delete_cascades_to_plans(self, session: Session) -> None:
        s = TestSession(name="cascade-test", status="pending", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": []},
        )
        session.add(p)
        session.flush()
        plan_id = p.id
        session.delete(s)
        session.flush()
        assert session.get(TestPlan, plan_id) is None

    def test_plan_delete_cascades_to_tasks(self, session: Session) -> None:
        s = TestSession(name="cascade-plan", status="pending", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": []},
        )
        session.add(p)
        session.flush()
        t = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/test"},
        )
        session.add(t)
        session.flush()
        task_id = t.id
        session.delete(p)
        session.flush()
        assert session.get(TestTask, task_id) is None

    def test_task_delete_cascades_to_result(self, session: Session) -> None:
        s = TestSession(name="cascade-result", status="executing", trigger_type="manual")
        session.add(s)
        session.flush()
        p = TestPlan(
            session_id=s.id,
            strategy_type="smoke",
            plan_json={"steps": []},
        )
        session.add(p)
        session.flush()
        t = TestTask(
            plan_id=p.id,
            task_type="api_test",
            task_config={"url": "/test"},
        )
        session.add(t)
        session.flush()
        r = TestResult(task_id=t.id, status="passed")
        session.add(r)
        session.flush()
        result_id = r.id
        session.delete(t)
        session.flush()
        assert session.get(TestResult, result_id) is None
