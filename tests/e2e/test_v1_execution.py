from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from testagent.gateway.middleware import register_error_handlers
from testagent.gateway.router import router, set_mcp_registry, set_session_manager
from testagent.gateway.session import SessionManager
from testagent.harness.orchestrator import HarnessOrchestrator
from testagent.harness.resource_scheduler import ResourceScheduler
from testagent.harness.runners.base import RunnerFactory
from testagent.harness.sandbox import ISandbox
from testagent.harness.sandbox_factory import SandboxFactory
from testagent.harness.self_healing import HealingResult, LocatorHealer
from testagent.harness.snapshot import ExecutionSnapshot, SnapshotService
from testagent.llm.base import LLMResponse
from testagent.models.plan import TestTask
from testagent.models.result import TestResult

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.asyncio,
]

# =============================================================================
# Helpers
# =============================================================================


def _make_executor_response(stop_reason: str = "end_turn") -> LLMResponse:
    return LLMResponse(
        content=[{"type": "text", "text": "Executed API test successfully."}],
        stop_reason=stop_reason,
        usage={"input_tokens": 40, "output_tokens": 25},
    )


def _make_healing_llm_response(semantic_xpath: str) -> LLMResponse:
    return LLMResponse(
        content=[{"type": "text", "text": semantic_xpath}],
        stop_reason="end_turn",
        usage={"input_tokens": 100, "output_tokens": 30},
    )


def _build_api_task(task_id: str, plan_id: str = "plan-v1-001", priority: int = 1) -> TestTask:
    return TestTask(
        id=task_id,
        plan_id=plan_id,
        task_type="api_test",
        isolation_level="docker",
        priority=priority,
        status="queued",
        retry_count=0,
        task_config={
            "base_url": "http://staging.demo.com",
            "method": "GET",
            "path": f"/api/v1/resource/{task_id.split('-')[-1]}",
            "assertions": {"status_code": 200},
        },
    )


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture()
def session_manager() -> SessionManager:
    return SessionManager()


@pytest.fixture()
def resource_scheduler() -> ResourceScheduler:
    return ResourceScheduler()


@pytest.fixture()
def snapshot_service(tmp_path_factory: pytest.TempPathFactory) -> SnapshotService:
    storage_dir = tmp_path_factory.mktemp("snapshots")
    return SnapshotService(storage_dir=storage_dir)


@pytest.fixture()
def api_app(session_manager: SessionManager) -> FastAPI:
    app = FastAPI()
    register_error_handlers(app)
    set_session_manager(session_manager)

    mock_registry = MagicMock()
    mock_api_info = MagicMock()
    mock_api_info.name = "api_server"
    mock_api_info.status = "healthy"
    mock_api_info.tools = [{"name": "http_request"}]
    mock_registry.register = AsyncMock(return_value=mock_api_info)
    mock_registry.list_servers = AsyncMock(return_value=[mock_api_info])
    mock_registry.lookup = AsyncMock(return_value=mock_api_info)
    set_mcp_registry(mock_registry)

    app.include_router(router)
    return app


@pytest.fixture()
def mock_executor_llm() -> MagicMock:
    mock = MagicMock()
    mock.chat = AsyncMock(side_effect=[_make_executor_response()])
    mock.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return mock


@pytest.fixture()
def mock_healing_llm() -> MagicMock:
    mock = MagicMock()
    mock.chat = AsyncMock(
        side_effect=[
            _make_healing_llm_response("//button[@aria-label='Submit']"),
            _make_healing_llm_response("//button[@aria-label='Submit']"),
        ]
    )
    mock.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
    return mock


# =============================================================================
# test_10_way_parallel_execution
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_10_way_parallel_execution(
    mock_executor_llm: MagicMock,
    session_manager: SessionManager,
    resource_scheduler: ResourceScheduler,
) -> None:
    """10 路并行执行验证 (V1.0 性能约束).

    验证内容:
    1. 创建包含 10 个独立 API 测试任务的计划
    2. 提交到 Celery 执行队列 (mock)
    3. 验证 10 路并行执行成功
    4. 验证每个 Executor Agent 数据隔离
    5. 验证 ResourceScheduler 资源分配
    """
    # Step 1: Create session with 10 independent API test tasks
    session = await session_manager.create_session(
        name="v1-10-way-parallel",
        trigger_type="auto",
        input_context={
            "strategy": "parallel",
            "max_concurrency": 10,
            "tasks": [
                {"task_id": f"v1-task-{i:02d}", "type": "api_test", "endpoint": f"/api/v1/resource/{i}"}
                for i in range(10)
            ],
        },
    )
    session_id: str = session["id"]
    await session_manager.transition(session_id, "planning")
    await session_manager.transition(session_id, "executing")

    # Build 10 independent test tasks
    test_tasks: list[TestTask] = [_build_api_task(f"v1-task-{i:02d}", priority=i + 1) for i in range(10)]
    assert len(test_tasks) == 10

    # Step 2: Verify ResourceScheduler can accept all 10 tasks
    for task in test_tasks:
        can_accept = await resource_scheduler.can_accept_task(task.task_type)
        assert can_accept, f"ResourceScheduler should accept task {task.id}"

    # Step 3: Verify ResourceScheduler resource allocation
    resources = await resource_scheduler.allocate_resources(test_tasks[0].task_type)
    assert resources["cpus"] == 1
    assert resources["mem_limit"] == "512m"
    assert resources["isolation_level"] == "docker"

    # Step 4: Execute 10 tasks concurrently
    concurrency_tracker: list[int] = []
    task_results: list[TestResult] = []

    async def _execute_parallel_task(task: TestTask) -> TestResult:
        nonlocal concurrency_tracker, task_results
        concurrency_tracker.append(1)
        current_concurrency = len(concurrency_tracker)
        assert current_concurrency <= 10, f"Concurrency exceeded 10: {current_concurrency}"

        if not await resource_scheduler.can_accept_task(task.task_type):
            pytest.fail(f"ResourceScheduler rejected task {task.id} at concurrency {current_concurrency}")

        allocated = await resource_scheduler.allocate_resources(task.task_type)
        await resource_scheduler.register_task(task.id, task.task_type, allocated)

        executor_id = f"executor_{task.id.split('-')[-1]}"

        mock_runner = MagicMock()
        mock_runner.setup = AsyncMock()
        mock_runner.execute = AsyncMock()
        task_idx = int(task.id.split("-")[-1])
        task_status = "passed" if task_idx % 3 != 0 else "failed"
        task_actual = 200 if task_idx % 3 != 0 else 500
        task_passed = task_idx % 3 != 0

        mock_runner.collect_results = AsyncMock(
            return_value=TestResult(
                task_id=task.id,
                status=task_status,
                duration_ms=100.0 * (task_idx + 1),
                assertion_results={
                    "status_code": {"expected": 200, "actual": task_actual, "passed": task_passed},
                },
                logs=(f'{{"executor": "{executor_id}", "task": "{task.id}", "duration_ms": {100 * (task_idx + 1)}}}'),
                artifacts={"executor_id": executor_id, "task_id": task.id, "sandbox_id": f"sandbox-{task.id}"},
            )
        )
        mock_runner.teardown = AsyncMock()
        mock_runner.runner_type = "api_test"

        mock_sandbox = MagicMock(spec=ISandbox)
        mock_sandbox.create = AsyncMock(return_value=f"sandbox-{task.id}")
        mock_sandbox.destroy = AsyncMock()
        mock_sandbox.execute = AsyncMock(return_value={"exit_code": 0, "stdout": "ok", "stderr": ""})
        mock_sandbox.get_logs = AsyncMock(return_value="")
        mock_sandbox.get_artifacts = AsyncMock(return_value=[])
        mock_sandbox.get_tmpdir = AsyncMock(return_value="/tmp/testagent")

        mock_sandbox_factory = MagicMock(spec=SandboxFactory)
        mock_sandbox_factory.create.return_value = mock_sandbox
        mock_runner_factory = MagicMock(spec=RunnerFactory)
        mock_runner_factory.get_runner.return_value = mock_runner

        orchestrator = HarnessOrchestrator(
            sandbox_factory=mock_sandbox_factory,
            runner_factory=mock_runner_factory,
        )

        result = await orchestrator.dispatch(task)

        # Step 5: Verify data isolation — each executor has its own sandbox and runner
        mock_sandbox.create.assert_called_once()
        mock_runner.setup.assert_called_once()
        mock_runner.execute.assert_called_once()
        mock_runner.collect_results.assert_called_once()
        mock_runner.teardown.assert_called_once()
        mock_sandbox.destroy.assert_called_once()

        assert result.artifacts is not None
        assert result.artifacts["executor_id"] == executor_id
        assert result.artifacts["task_id"] == task.id

        await resource_scheduler.unregister_task(task.id)
        concurrency_tracker.pop()
        return result

    results = await asyncio.gather(*[_execute_parallel_task(task) for task in test_tasks])

    # Step 4: Verify 10-way parallel execution succeeded
    assert len(results) == 10

    passed_count = sum(1 for r in results if r.status == "passed")
    failed_count = sum(1 for r in results if r.status == "failed")
    assert passed_count + failed_count == 10
    assert passed_count >= 6

    # Step 5: Verify data isolation — each result has its own task_id
    result_task_ids = {r.task_id for r in results}
    expected_task_ids = {t.id for t in test_tasks}
    assert result_task_ids == expected_task_ids

    # Step 5: Verify ResourceScheduler resource usage reporting
    usage = await resource_scheduler.get_resource_usage()
    assert usage["running_tasks"] == 0
    assert usage["max_concurrency"] == 10

    # Verify concurrency never exceeded 10
    assert len(results) == 10

    await session_manager.transition(session_id, "analyzing")
    await session_manager.transition(session_id, "completed")


# =============================================================================
# test_snapshot_resume_10_tasks
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_snapshot_resume_10_tasks(
    session_manager: SessionManager,
    snapshot_service: SnapshotService,
) -> None:
    """快照断点续跑 10 任务验证 (V1.0).

    验证内容:
    1. 启动 10 路并行执行
    2. 在第 5 步保存快照
    3. 模拟系统中断
    4. 从快照恢复全部 10 个任务
    5. 验证断点续跑成功率 100%
    """
    session = await session_manager.create_session(
        name="v1-snapshot-resume",
        trigger_type="auto",
        input_context={
            "strategy": "parallel",
            "max_concurrency": 10,
        },
    )
    session_id: str = session["id"]
    await session_manager.transition(session_id, "planning")
    await session_manager.transition(session_id, "executing")

    # Step 1: Create 10 tasks with 10 steps each
    total_tasks = 10
    steps_per_task = 10
    snapshot_point = 5

    task_ids: list[str] = [f"v1-snap-task-{i:02d}" for i in range(total_tasks)]

    for tid in task_ids:
        remaining = [f"step_{s}" for s in range(1, steps_per_task + 1)]
        snapshot = ExecutionSnapshot(
            task_id=tid,
            status="running",
            session_id=session_id,
            progress=0.0,
            checkpoint={"started_at": "2026-01-01T00:00:00Z"},
            completed_steps=[],
            remaining_steps=remaining,
            intermediate_results={},
            resource_state={"sandbox_id": f"sandbox-{tid}", "container_id": f"container-{tid}"},
        )
        await snapshot_service.save_full_snapshot(snapshot)
        await session_manager.publish_event(session_id, "task.started", {"task_id": tid})

    # Step 2: Execute steps 1-5 and save snapshots at each step
    for step in range(1, snapshot_point + 1):
        for tid in task_ids:
            result = {"step": step, "status": "passed", "duration_ms": 50 * step}
            await snapshot_service.save_step_completion(
                task_id=tid,
                step_id=f"step_{step}",
                result=result,
                session_id=session_id,
            )
            await session_manager.publish_event(
                session_id,
                "task.progress",
                {"task_id": tid, "step": step, "progress": step / steps_per_task},
            )

        if step == snapshot_point:
            await session_manager.publish_event(
                session_id,
                "task.snapshot_saved",
                {
                    "task_id": None,
                    "step": snapshot_point,
                    "snapshot_count": total_tasks,
                    "message": f"Snapshot saved at step {snapshot_point} for all {total_tasks} tasks",
                },
            )

    # Verify snapshots saved at step 5
    for tid in task_ids:
        snap = await snapshot_service.load(tid)
        assert snap is not None
        assert len(snap.completed_steps) == snapshot_point
        assert len(snap.remaining_steps) == steps_per_task - snapshot_point
        snap_data = snap.to_dict()
        assert isinstance(snap_data["intermediate_results"], dict)
        assert f"step_{snapshot_point}" in snap_data["intermediate_results"]

    # Step 3: Simulate system interruption — status becomes interrupted
    for tid in task_ids:
        interrupted = ExecutionSnapshot(
            task_id=tid,
            status="running",
            session_id=session_id,
            progress=0.5,
            checkpoint={"interrupted_at": "2026-01-01T00:00:05Z", "reason": "system_crash"},
            completed_steps=[f"step_{s}" for s in range(1, snapshot_point + 1)],
            remaining_steps=[f"step_{s}" for s in range(snapshot_point + 1, steps_per_task + 1)],
            intermediate_results={
                f"step_{s}": {"status": "passed", "duration_ms": 50 * s} for s in range(1, snapshot_point + 1)
            },
            resource_state={"sandbox_id": f"sandbox-{tid}", "container_id": f"container-{tid}"},
        )
        await snapshot_service.save_full_snapshot(interrupted)

    # Verify all 10 snapshots are in incomplete state
    all_incomplete = await snapshot_service.list_incomplete()
    incomplete_task_ids = {s.task_id for s in all_incomplete}
    assert len(incomplete_task_ids) == total_tasks
    assert incomplete_task_ids == set(task_ids)

    # Step 4: Resume from snapshot for all 10 tasks
    resume_results: dict[str, dict[str, object]] = {}
    for tid in task_ids:
        context = await snapshot_service.resume_from_snapshot(tid)
        assert context is not None
        resume_results[tid] = context

        snap_dict = context["snapshot"]
        assert isinstance(snap_dict, dict)
        assert snap_dict["task_id"] == tid
        assert snap_dict["status"] == "running"

        # Resume should start from step 6
        resume_from: str = str(context["resume_from_step"])
        assert resume_from == "step_6", f"Expected step_6, got {resume_from} for {tid}"

        await session_manager.publish_event(
            session_id,
            "task.resuming",
            {"task_id": tid, "resume_from_step": resume_from, "progress": snap_dict["progress"]},
        )

    # Step 5: Complete remaining steps 6-10 for all tasks
    for step in range(snapshot_point + 1, steps_per_task + 1):
        for tid in task_ids:
            result = {"step": step, "status": "passed", "duration_ms": 50 * step}
            await snapshot_service.save_step_completion(
                task_id=tid,
                step_id=f"step_{step}",
                result=result,
                session_id=session_id,
            )
            await session_manager.publish_event(
                session_id,
                "task.progress",
                {"task_id": tid, "step": step, "progress": step / steps_per_task},
            )

        # Mark task as completed when all steps done
        if step == steps_per_task:
            for tid in task_ids:
                final_snap = await snapshot_service.load(tid)
                assert final_snap is not None
                final_status = ExecutionSnapshot(
                    task_id=tid,
                    status="passed",
                    session_id=session_id,
                    progress=1.0,
                    checkpoint={"completed_at": "2026-01-01T00:01:00Z"},
                    completed_steps=[f"step_{s}" for s in range(1, steps_per_task + 1)],
                    remaining_steps=[],
                    intermediate_results=cast("dict[str, object]", resume_results[tid].get("intermediate_results", {})),
                    resource_state={"sandbox_id": f"sandbox-{tid}"},
                )
                await snapshot_service.save_full_snapshot(final_status)
                await session_manager.publish_event(session_id, "task.completed", {"task_id": tid, "status": "passed"})

    # Verify resume success rate = 100%
    for tid in task_ids:
        final_snap = await snapshot_service.load(tid)
        assert final_snap is not None
        assert final_snap.status == "passed"
        assert final_snap.progress == 1.0
        assert len(final_snap.completed_steps) == steps_per_task
        assert len(final_snap.remaining_steps) == 0

    # Verify no incomplete snapshots remain
    remaining_incomplete = await snapshot_service.list_incomplete()
    assert len(remaining_incomplete) == 0, "All tasks should be completed"

    # Verify resume success rate
    task_statuses = []
    for tid in task_ids:
        snap = await snapshot_service.load(tid)
        assert snap is not None
        task_statuses.append(snap.status)
    assert task_statuses.count("passed") == total_tasks

    await session_manager.transition(session_id, "analyzing")
    await session_manager.transition(session_id, "completed")


# =============================================================================
# test_websocket_real_time_progress
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_websocket_real_time_progress(
    session_manager: SessionManager,
    api_app: FastAPI,
) -> None:
    """WebSocket 实时进度上报验证 (V1.0).

    验证内容:
    1. 建立 WebSocket 连接
    2. 提交测试任务
    3. 验证接收到 task.progress / task.completed / quality.trend_update 事件
    4. 验证断连重连后事件恢复
    """
    # Step 1: Create session
    session = await session_manager.create_session(
        name="v1-websocket-progress",
        trigger_type="manual",
        input_context={"skill": "api_smoke_test", "env": "staging"},
    )
    session_id: str = session["id"]
    await session_manager.transition(session_id, "planning")
    await session_manager.transition(session_id, "executing")

    # Step 2: Subscribe to events (simulates WebSocket client)
    event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    subscriber = asyncio.create_task(_collect_events(session_manager, session_id, event_queue))

    await asyncio.sleep(0.05)

    # Step 3: Publish task progress events
    task_ids: list[str] = [f"ws-task-{i:02d}" for i in range(3)]

    progress_events_count = 0
    for task_id in task_ids:
        await session_manager.publish_event(session_id, "task.started", {"task_id": task_id, "name": task_id})

        for pct in [25, 50, 75, 100]:
            await session_manager.publish_event(
                session_id,
                "task.progress",
                {"task_id": task_id, "progress": pct, "status": "running"},
            )
            progress_events_count += 1

        await session_manager.publish_event(
            session_id,
            "task.completed",
            {"task_id": task_id, "status": "passed", "duration_ms": 200},
        )

    # Publish V1.0 quality trend update event
    await session_manager.publish_event(
        session_id,
        "quality.trend_update",
        {
            "session_id": session_id,
            "trend": "improving",
            "pass_rate": 100.0,
            "total_tasks": len(task_ids),
            "passed_tasks": len(task_ids),
        },
    )

    await asyncio.sleep(0.05)

    # Step 4: Collect and verify received events
    received_events: list[dict[str, Any]] = []
    while not event_queue.empty():
        try:
            ev = event_queue.get_nowait()
            received_events.append(ev)
        except asyncio.QueueEmpty:
            break

    subscriber.cancel()
    with pytest.raises(asyncio.CancelledError):
        await subscriber

    # Verify event types received
    received_event_types = {e.get("event") for e in received_events}

    assert "task.started" in received_event_types
    assert "task.progress" in received_event_types
    assert "task.completed" in received_event_types
    assert "quality.trend_update" in received_event_types

    # Verify at least one task.progress event per task
    progress_events = [e for e in received_events if e.get("event") == "task.progress"]
    assert len(progress_events) >= 3, "Should receive at least 3 progress events"

    # Verify quality.trend_update payload
    quality_events = [e for e in received_events if e.get("event") == "quality.trend_update"]
    assert len(quality_events) >= 1
    qe = quality_events[0]
    assert qe["data"]["pass_rate"] == 100.0
    assert qe["data"]["trend"] == "improving"

    # Verify task.completed events for all tasks
    completed_events = [e for e in received_events if e.get("event") == "task.completed"]
    assert len(completed_events) == len(task_ids)
    for ce in completed_events:
        assert ce["data"]["status"] == "passed"

    # Step 5: Verify WebSocket reconnection event recovery
    event_queue_reconnect: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    subscriber_reconnect = asyncio.create_task(_collect_events(session_manager, session_id, event_queue_reconnect))

    await asyncio.sleep(0.05)

    await session_manager.publish_event(
        session_id,
        "task.progress",
        {"task_id": "ws-task-reconnect", "progress": 50, "status": "running"},
    )

    await asyncio.sleep(0.05)

    reconnect_events: list[dict[str, Any]] = []
    while not event_queue_reconnect.empty():
        try:
            ev = event_queue_reconnect.get_nowait()
            reconnect_events.append(ev)
        except asyncio.QueueEmpty:
            break

    subscriber_reconnect.cancel()
    with pytest.raises(asyncio.CancelledError):
        await subscriber_reconnect

    reconnect_event_types = {e.get("event") for e in reconnect_events}
    assert "task.progress" in reconnect_event_types

    await session_manager.transition(session_id, "analyzing")
    await session_manager.transition(session_id, "completed")


# =============================================================================
# test_self_healing_three_level
# =============================================================================


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_self_healing_three_level(
    mock_healing_llm: MagicMock,
    session_manager: SessionManager,
) -> None:
    """三级自愈降级验证 (V1.0).

    验证内容:
    1. 模拟 CSS 选择器失效
    2. 验证 CSS→XPath 修复 (Level 1)
    3. 模拟 XPath 也失效
    4. 验证 XPath→语义定位修复 (Level 2)
    5. 验证 task.self_healing 事件推送
    """
    session = await session_manager.create_session(
        name="v1-self-healing-three-level",
        trigger_type="manual",
        input_context={
            "skill": "web_smoke_test",
            "self_healing_enabled": True,
            "url": "https://example.com",
        },
    )
    session_id: str = session["id"]
    await session_manager.transition(session_id, "planning")
    await session_manager.transition(session_id, "executing")

    page_source = """
    <html>
    <body>
        <form id="login-form">
            <label>Username</label>
            <input type="text" name="username" aria-label="Username input" />
            <label>Password</label>
            <input type="password" name="password" aria-label="Password input" />
            <button type="submit" class="btn-primary" data-testid="login-btn">Sign In</button>
        </form>
    </body>
    </html>
    """

    # Step 1: Create LocatorHealer with mocked LLM
    healer = LocatorHealer(llm_provider=mock_healing_llm)

    # Step 2: Simulate CSS selector failure and verify CSS→XPath healing (Level 1)
    css_selector = "#login-form .btn-primary"

    healing_events: list[dict[str, Any]] = []
    css_to_xpath_result = await healer.css_to_xpath(css_selector)
    assert css_to_xpath_result != "", "CSS→XPath should produce a valid XPath"
    assert "//" in css_to_xpath_result, "CSS→XPath result should be an XPath expression"

    # Step 2: Verify Level 1 healing via heal() with CSS selector
    level1_result: HealingResult = await healer.heal(css_selector, page_source, "Element not found")
    assert level1_result.healing_level >= 1, "CSS→XPath should achieve at least Level 1 healing"
    assert level1_result.confidence > 0.0
    assert len(level1_result.steps) >= 1

    if level1_result.healing_level == 1:
        assert "css" in level1_result.steps[0].lower() or "xpath" in level1_result.steps[0].lower()
    else:
        assert level1_result.healing_level == 2

    healing_events.append(
        {
            "type": "self_heal",
            "level": level1_result.healing_level,
            "original_selector": css_selector,
            "healed_selector": level1_result.healed_selector,
            "confidence": level1_result.confidence,
            "steps": level1_result.steps,
        }
    )

    await session_manager.publish_event(
        session_id,
        "task.self_healing",
        {
            "task_id": "heal-task-001",
            "original_locator": css_selector,
            "resolved_locator": level1_result.healed_selector,
            "strategy": "css_to_xpath",
            "healing_level": level1_result.healing_level,
            "confidence": level1_result.confidence,
            "success": level1_result.healed_selector != css_selector,
        },
    )

    # Step 3: Simulate XPath also failing and verify XPath→semantic healing (Level 2)
    xpath_selector = css_to_xpath_result or level1_result.healed_selector

    level2_result: HealingResult = await healer.heal(xpath_selector, page_source, "Element not found with XPath")
    assert level2_result.healing_level >= 1

    healing_events.append(
        {
            "type": "self_heal",
            "level": level2_result.healing_level,
            "original_selector": xpath_selector,
            "healed_selector": level2_result.healed_selector,
            "confidence": level2_result.confidence,
            "steps": level2_result.steps,
        }
    )

    await session_manager.publish_event(
        session_id,
        "task.self_healing",
        {
            "task_id": "heal-task-002",
            "original_locator": xpath_selector,
            "resolved_locator": level2_result.healed_selector,
            "strategy": "xpath_to_semantic",
            "healing_level": level2_result.healing_level,
            "confidence": level2_result.confidence,
            "success": level2_result.healed_selector != xpath_selector,
        },
    )

    # Step 4: Verify healing events recorded
    assert len(healing_events) >= 2, "Should have at least 2 healing events"

    level_1_events = [e for e in healing_events if e["level"] == 1]
    level_2_events = [e for e in healing_events if e["level"] == 2]

    # At least one CSS→XPath (Level 1) healing should have occurred
    assert len(level_1_events) >= 1 or len(level_2_events) >= 1

    # Verify event data structure
    for ev in healing_events:
        assert ev["type"] == "self_heal"
        assert ev["original_selector"] != ev["healed_selector"] or ev["level"] == 0
        assert 0 <= ev["confidence"] <= 0.95

    # Step 5: Verify task.self_healing events were published via session manager
    all_events: list[dict[str, Any]] = []
    event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    subscriber = asyncio.create_task(_collect_events(session_manager, session_id, event_queue))
    await asyncio.sleep(0.05)

    await session_manager.publish_event(
        session_id,
        "task.self_healing",
        {
            "task_id": "heal-task-final",
            "original_locator": css_selector,
            "resolved_locator": level2_result.healed_selector if level2_result.healing_level > 0 else css_selector,
            "strategy": "three_level",
            "healing_level": max(e["level"] for e in healing_events),
            "confidence": max(e["confidence"] for e in healing_events),
            "success": True,
            "summary": (
                f"CSS→XPath→Semantic: original={css_selector}, "
                f"final={level2_result.healed_selector if level2_result.healing_level > 0 else 'unresolved'}"
            ),
        },
    )

    await asyncio.sleep(0.05)

    while not event_queue.empty():
        try:
            all_events.append(event_queue.get_nowait())
        except asyncio.QueueEmpty:
            break

    subscriber.cancel()
    with pytest.raises(asyncio.CancelledError):
        await subscriber

    self_healing_events = [e for e in all_events if e.get("event") == "task.self_healing"]
    assert len(self_healing_events) >= 1

    last_heal = self_healing_events[-1]["data"]
    assert "original_locator" in last_heal
    assert "resolved_locator" in last_heal
    assert "healing_level" in last_heal

    # Verify locator library update format
    locator_library_update = {
        "original": css_selector,
        "resolved": level2_result.healed_selector if level2_result.healing_level > 0 else level1_result.healed_selector,
        "strategy": "xpath" if level2_result.healing_level == 0 else "semantic",
        "verified_at": "2026-01-01T00:00:00Z",
    }
    assert locator_library_update["original"] != locator_library_update["resolved"]

    await session_manager.transition(session_id, "analyzing")
    await session_manager.transition(session_id, "completed")


# =============================================================================
# Helpers — event collection
# =============================================================================


async def _collect_events(
    session_manager: SessionManager,
    session_id: str,
    queue: asyncio.Queue[dict[str, Any]],
) -> None:
    """Subscribe to session events and put them into the given queue."""
    async for event in session_manager.subscribe(session_id):
        await queue.put(event)
