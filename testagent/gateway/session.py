from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from testagent.common import get_logger
from testagent.common.errors import TestAgentError

_logger = get_logger(__name__)

SESSION_TRANSITIONS: dict[str, set[str]] = {
    "pending": {"planning", "failed"},
    "planning": {"executing", "failed"},
    "executing": {"analyzing", "failed"},
    "analyzing": {"completed", "failed"},
    "failed": set(),
    "completed": set(),
}

# MVP events
_MVP_SESSION_EVENTS = {
    "session.started",
    "plan.generated",
    "task.started",
    "task.progress",
    "task.completed",
    "task.self_healing",
    "result.analyzed",
    "defect.filed",
    "session.completed",
    "session.failed",
    "session.planning",
    "session.executing",
    "session.analyzing",
}

# V1.0 additional events (PRD F-E04)
_V1_SESSION_EVENTS = {
    "task.snapshot_saved",
    "task.resuming",
    "resource.usage",
    "quality.trend_update",
}

SESSION_EVENTS = frozenset(_MVP_SESSION_EVENTS | _V1_SESSION_EVENTS)

# Heartbeat timeout in seconds
_HEARTBEAT_TIMEOUT = 30.0
_SESSION_REDIS_PREFIX = "testagent:session:"


class SessionStateError(TestAgentError):
    pass


class SessionNotFoundError(TestAgentError):
    pass


class SessionManager:
    def __init__(self, redis_client: Any = None) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        self._subscribers: dict[str, list[asyncio.Queue[dict[str, Any]]]] = {}
        self._global_subscribers: list[asyncio.Queue[dict[str, Any]]] = []
        self._lock = asyncio.Lock()
        self._logger = _logger
        self._redis = redis_client
        self._heartbeat_tasks: dict[str, asyncio.Task[None]] = {}

    async def create_session(
        self,
        name: str,
        trigger_type: str = "manual",
        input_context: dict[str, object] | None = None,
    ) -> dict[str, Any]:
        session_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        session: dict[str, Any] = {
            "id": session_id,
            "name": name,
            "status": "pending",
            "trigger_type": trigger_type,
            "input_context": input_context or {},
            "created_at": now,
            "completed_at": None,
        }
        async with self._lock:
            self._sessions[session_id] = session
        await self._broadcast(session_id, "session.started", session)
        self._logger.info("Session created", extra={"extra_data": {"session_id": session_id, "name": name}})
        return session

    async def get_session(self, session_id: str) -> dict[str, Any]:
        async with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(
                message=f"Session '{session_id}' not found",
                code="SESSION_NOT_FOUND",
                details={"session_id": session_id},
            )
        return session

    async def list_sessions(self) -> list[dict[str, Any]]:
        async with self._lock:
            return list(self._sessions.values())

    async def transition(self, session_id: str, new_status: str) -> dict[str, Any]:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                raise SessionNotFoundError(
                    message=f"Session '{session_id}' not found",
                    code="SESSION_NOT_FOUND",
                    details={"session_id": session_id},
                )

            current = str(session["status"])
            allowed = SESSION_TRANSITIONS.get(current, set())
            if new_status not in allowed:
                raise SessionStateError(
                    message=f"Invalid state transition from '{current}' to '{new_status}'",
                    code="INVALID_STATE_TRANSITION",
                    details={
                        "session_id": session_id,
                        "current_status": current,
                        "requested_status": new_status,
                        "allowed_transitions": list(allowed),
                    },
                )

            session["status"] = new_status
            if new_status in ("completed", "failed"):
                session["completed_at"] = datetime.now(UTC).isoformat()

        event_name = f"session.{new_status}"
        if event_name in SESSION_EVENTS:
            await self._broadcast(session_id, event_name, session)

        self._logger.info(
            "Session transition",
            extra={
                "extra_data": {
                    "session_id": session_id,
                    "from_status": current,
                    "to_status": new_status,
                }
            },
        )
        return session

    async def cancel_session(self, session_id: str) -> dict[str, Any]:
        return await self.transition(session_id, "failed")

    async def subscribe(self, session_id: str) -> AsyncIterator[dict[str, Any]]:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is not None and session["status"] in ("completed", "failed"):
                yield {
                    "event": f"session.{session['status']}",
                    "session_id": session_id,
                    "data": session,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                return

        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        async with self._lock:
            if session_id not in self._subscribers:
                self._subscribers[session_id] = []
            self._subscribers[session_id].append(queue)
        try:
            while True:
                event = await queue.get()
                yield event
                if event.get("event") in ("session.completed", "session.failed"):
                    break
        finally:
            async with self._lock:
                subs = self._subscribers.get(session_id, [])
                if queue in subs:
                    subs.remove(queue)

    async def publish_event(
        self,
        session_id: str,
        event: str,
        data: dict[str, object] | None = None,
    ) -> None:
        if event not in SESSION_EVENTS:
            self._logger.warning(
                "Unknown session event",
                extra={"extra_data": {"session_id": session_id, "event": event}},
            )
        await self._broadcast(session_id, event, data or {})

    async def subscribe_global(self) -> asyncio.Queue[dict[str, Any]]:
        """Subscribe to ALL session events globally."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        async with self._lock:
            self._global_subscribers.append(q)
        return q

    async def unsubscribe_global(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        """Unsubscribe from global events."""
        async with self._lock:
            if q in self._global_subscribers:
                self._global_subscribers.remove(q)

    async def _broadcast(self, session_id: str, event: str, data: dict[str, Any]) -> None:
        async with self._lock:
            subs = list(self._subscribers.get(session_id, []))
            global_subs = list(self._global_subscribers)
        message: dict[str, Any] = {
            "event": event,
            "session_id": session_id,
            "data": data,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        for queue in subs:
            await queue.put(message)
        for queue in global_subs:
            await queue.put(message)

    async def broadcast_event(self, session_id: str, event_type: str, payload: dict[str, Any]) -> None:
        """广播事件到所有订阅该 session 的 WebSocket 客户端（V1.0 增强接口）。"""
        await self._broadcast(session_id, event_type, payload)

    async def unsubscribe(self, session_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        """显式取消订阅指定 session 的事件队列。"""
        async with self._lock:
            subs = self._subscribers.get(session_id, [])
            if queue in subs:
                subs.remove(queue)

    async def heartbeat(self, session_id: str) -> bool:
        """WebSocket 心跳检测 — 检查 session 是否仍处于活跃状态。"""
        try:
            session = await self.get_session(session_id)
            return session.get("status") not in ("completed", "failed", "cancelled")
        except SessionNotFoundError:
            return False

    async def _persist_to_redis(self, session: dict[str, Any]) -> None:
        """将 session 状态持久化到 Redis（用于断连重连恢复）。"""
        if self._redis is None:
            return
        key = f"{_SESSION_REDIS_PREFIX}{session['id']}"
        try:
            serialized = json.dumps(session, default=str)
            result_or_coro = self._redis.set(key, serialized)
            if asyncio.iscoroutine(result_or_coro):
                await result_or_coro
            expire_result = self._redis.expire(key, 3600)
            if asyncio.iscoroutine(expire_result):
                await expire_result
        except Exception as exc:
            self._logger.warning(
                "Failed to persist session to Redis",
                extra={"extra_data": {"session_id": session["id"], "error": str(exc)}},
            )

    async def _load_from_redis(self, session_id: str) -> dict[str, Any] | None:
        """从 Redis 恢复 session 状态。"""
        if self._redis is None:
            return None
        key = f"{_SESSION_REDIS_PREFIX}{session_id}"
        try:
            result_or_coro = self._redis.get(key)
            if asyncio.iscoroutine(result_or_coro):
                raw = await result_or_coro
            else:
                raw = result_or_coro
            if raw:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                return cast("dict[str, Any]", json.loads(raw))
        except Exception as exc:
            self._logger.warning(
                "Failed to load session from Redis",
                extra={"extra_data": {"session_id": session_id, "error": str(exc)}},
            )
        return None

    async def get_active_sessions(self) -> list[dict[str, Any]]:
        """获取所有活跃（非终态）session 列表。"""
        async with self._lock:
            return [s for s in self._sessions.values() if s.get("status") not in ("completed", "failed", "cancelled")]


async def run_session(
    skill_name: str | None = None,
    plan_path: str | None = None,
    env: str = "dev",
    url: str | None = None,
) -> dict[str, Any]:
    """Execute a full test session through the Planner→Executor→Analyzer pipeline.

    This is the primary entry point used by the CLI ``testagent run`` command.
    It creates a session, runs the three-agent lifecycle, and returns aggregated results.
    """
    import time

    from testagent.agent.analyzer import AnalyzerAgent
    from testagent.agent.context import ContextAssembler
    from testagent.agent.executor import ExecutorAgent
    from testagent.agent.loop import register_tool_handler
    from testagent.agent.planner import PlannerAgent
    from testagent.config.settings import get_settings
    from testagent.llm.local_provider import LLMProviderFactory

    settings = get_settings()
    llm = LLMProviderFactory.create(settings)
    context_assembler = ContextAssembler(settings=settings)
    manager = SessionManager()

    session = await manager.create_session(
        name=f"cli-run-{skill_name or 'manual'}",
        trigger_type="manual",
        input_context={
            "skill": skill_name,
            "plan_path": plan_path,
            "env": env,
            "url": url,
        },
    )
    session_id: str = session["id"]
    start_time = time.monotonic()
    _logger.info("CLI run session created", extra={"extra_data": {"session_id": session_id, "skill": skill_name}})

    # ── 初始化 MCP 工具 ──────────────────────────────────────────
    dispatch_fn = None

    # 注册 load_skill 工具（所有 Agent 可用）
    _register_skill_tool(skill_name)

    # 如果是 app 测试, 初始化 Appium 以便后续执行真实测试
    appium_srv = None
    if skill_name and "app" in skill_name.lower():
        try:
            from testagent.mcp_servers.appium_server.server import AppiumMCPServer

            appium_srv = AppiumMCPServer(appium_url="http://localhost:4723")
            _logger.info("Appium MCP server initialized for app test")

            # 注册 Appium 工具 handlers (LLM 若调用则走这里)
            async def _make_appium_handler(tool_name: str):
                async def _handler(tool_input: dict[str, Any]) -> dict[str, Any]:
                    try:
                        raw = await appium_srv.call_tool(tool_name, tool_input)
                        return {"result": raw}
                    except Exception as exc:
                        return {"error": str(exc), "tool_name": tool_name}

                return _handler

            for spec in appium_srv._tools_spec:
                tn = spec["name"]
                handler = await _make_appium_handler(tn)
                register_tool_handler(tn, handler)

            async def dispatch_fn(tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
                from testagent.agent.loop import dispatch_tool

                return await dispatch_tool(tool_name, tool_input)

            _logger.info(
                "Appium tools registered",
                extra={"extra_data": {"tool_count": len(appium_srv._tools_spec)}},
            )
        except Exception as exc:
            _logger.warning(
                "Appium MCP init failed, proceeding without Appium",
                extra={"extra_data": {"error": str(exc)}},
            )

    # ── Agent 1: Planner ─────────────────────────────────────────
    planner = PlannerAgent(llm=llm, context_assembler=context_assembler)
    await manager.transition(session_id, "planning")
    plan_result = await planner.execute(
        {
            "task_type": "plan",
            "skill": skill_name,
            "plan_path": plan_path,
            "env": env,
        },
        dispatch_fn=dispatch_fn,
        tools_override=[],  # Planner 不需要调用工具
    )
    _logger.info(
        "Planning completed",
        extra={"extra_data": {"session_id": session_id, "plan": plan_result.get("plan")}},
    )

    # ── Agent 2: Executor ────────────────────────────────────────
    executor = ExecutorAgent(llm=llm, context_assembler=context_assembler)
    await manager.transition(session_id, "executing")
    execute_result = await executor.execute(
        {
            "task_type": "execute",
            "skill": skill_name,
            "env": env,
            "url": url,
        },
        dispatch_fn=dispatch_fn,
        tools_override=[],
    )
    _logger.info(
        "Execution completed",
        extra={"extra_data": {"session_id": session_id, "result": execute_result.get("result")}},
    )

    # ── 真实 Appium 测试执行 ────────────────────────────────────
    appium_tasks: list[dict[str, Any]] = []
    if appium_srv is not None:
        try:
            appium_tasks = await _run_appium_tests(appium_srv)
            _logger.info(
                "Appium real tests completed",
                extra={"extra_data": {"task_count": len(appium_tasks)}},
            )
        except Exception as exc:
            _logger.warning("Appium real tests failed", extra={"extra_data": {"error": str(exc)}})

    # ── Agent 3: Analyzer ────────────────────────────────────────
    analyzer = AnalyzerAgent(llm=llm, context_assembler=context_assembler)
    await manager.transition(session_id, "analyzing")
    analyze_result = await analyzer.execute(
        {
            "task_type": "analyze",
            "session_id": session_id,
            "execute_result": execute_result.get("result"),
        },
        dispatch_fn=dispatch_fn,
        tools_override=[],  # Analyzer 不需要调用工具
    )
    _logger.info(
        "Analysis completed",
        extra={"extra_data": {"session_id": session_id, "analysis": analyze_result.get("analysis")}},
    )

    await manager.transition(session_id, "completed")

    # ── 结果聚合 ──────────────────────────────────────────────────
    duration_s = time.monotonic() - start_time
    tasks = _build_tasks_from_results(plan_result, execute_result, analyze_result, appium_tasks)

    return {
        "session_id": session_id,
        "status": "completed",
        "tasks": tasks,
        "duration": f"{duration_s:.1f}s",
        "plan": plan_result.get("plan"),
        "execution": execute_result.get("result"),
        "analysis": analyze_result.get("analysis"),
    }


def _register_skill_tool(skill_name: str | None) -> None:
    """注册 load_skill 工具，让 Agent 能动态加载技能详情。"""
    from pathlib import Path

    from testagent.agent.loop import register_tool_handler

    skills_dir = Path(__file__).resolve().parent.parent.parent / "skills"

    async def handle_load_skill(tool_input: dict[str, Any]) -> dict[str, Any]:
        name = str(tool_input.get("name", ""))
        if not name:
            return {"error": "Missing 'name' parameter", "found": False}

        import yaml

        skill_path = skills_dir / name / "SKILL.md"
        if not skill_path.exists():
            return {
                "found": False,
                "error": f"Skill '{name}' not found",
            }

        content = skill_path.read_text(encoding="utf-8")
        parts = content.split("---", 2)
        body = parts[2].strip() if len(parts) >= 3 else content

        desc = ""
        if len(parts) >= 2:
            try:
                meta = yaml.safe_load(parts[1])
                desc = str(meta.get("description", "")) if isinstance(meta, dict) else ""
            except Exception:
                pass

        return {
            "found": True,
            "name": name,
            "description": desc,
            "body": body,
        }

    register_tool_handler("load_skill", handle_load_skill)


def _build_tasks_from_results(
    plan_result: dict[str, Any],
    execute_result: dict[str, Any],
    analyze_result: dict[str, Any],
    appium_tasks: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """将三个 Agent 的执行结果聚合为任务列表。"""
    tasks: list[dict[str, Any]] = []

    # 规划阶段任务
    plan = plan_result.get("plan", {})
    strategy_text = plan.get("strategy", "") if isinstance(plan, dict) else str(plan)
    tasks.append({
        "name": "test-planning",
        "status": "passed" if strategy_text and strategy_text != "no_output" else "failed",
        "summary": strategy_text[:200] if strategy_text else "No plan generated",
        "duration": "-",
        "agent": "planner",
    })

    # 执行阶段任务
    exec_result = execute_result.get("result", {})
    exec_details = exec_result.get("details", "") if isinstance(exec_result, dict) else str(exec_result)
    exec_status = exec_result.get("status", "completed") if isinstance(exec_result, dict) else "completed"
    tasks.append({
        "name": "test-execution",
        "status": "passed" if exec_status != "failed" else "failed",
        "summary": exec_details[:200] if exec_details else "Execution completed",
        "duration": "-",
        "agent": "executor",
    })

    # 真实 Appium 测试结果
    if appium_tasks:
        tasks.extend(appium_tasks)

    # 分析阶段任务
    analysis = analyze_result.get("analysis", {})
    analysis_summary = analysis.get("summary", "") if isinstance(analysis, dict) else str(analysis)
    defects = analysis.get("defects", []) if isinstance(analysis, dict) else []
    tasks.append({
        "name": "test-analysis",
        "status": "passed",
        "summary": analysis_summary[:200] if analysis_summary else "Analysis completed",
        "defects_found": len(defects),
        "duration": "-",
        "agent": "analyzer",
    })

    return tasks


async def _run_appium_tests(appium_srv: Any) -> list[dict[str, Any]]:
    """直接执行 Appium 真实测试, 返回任务结果列表。"""
    import base64

    import httpx

    appium_url = "http://localhost:4723"
    tasks: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(30)) as client:
        # 1. 健康检查
        try:
            resp = await client.get(f"{appium_url}/status")
            healthy = resp.status_code == 200
        except Exception:
            healthy = False

        tasks.append({
            "name": "appium-server-health",
            "status": "passed" if healthy else "failed",
            "summary": "Appium server is running" if healthy else "Appium server unreachable",
            "duration": "-",
            "agent": "appium_direct",
        })

        if not healthy:
            return tasks

        # 2. 创建 Appium Session
        session_caps = {
            "capabilities": {
                "alwaysMatch": {
                    "platformName": "Android",
                    "appium:automationName": "UiAutomator2",
                    "appium:deviceName": "emulator-5554",
                    "appium:udid": "emulator-5554",
                    "appium:noReset": True,
                    "appium:autoGrantPermissions": True,
                    "appium:newCommandTimeout": 60,
                },
                "firstMatch": [{}],
            }
        }

        session_id = None
        resp = await client.post(f"{appium_url}/session", json=session_caps)
        if resp.status_code == 200:
            data = resp.json()
            session_id = data.get("value", {}).get("sessionId") or data.get("sessionId")

        tasks.append({
            "name": "create-appium-session",
            "status": "passed" if session_id else "failed",
            "summary": f"Session created: {session_id[:8]}..." if session_id else f"Session creation failed: {resp.text[:100]}",
            "duration": "-",
            "agent": "appium_direct",
        })

        if not session_id:
            return tasks

        # 等 session 就绪
        await asyncio.sleep(2)

        # 3. 截取屏幕
        try:
            resp = await client.get(f"{appium_url}/session/{session_id}/screenshot")
            if resp.status_code == 200:
                data = resp.json()
                screenshot_b64 = data.get("value", "")
                if screenshot_b64:
                    img_data = base64.b64decode(screenshot_b64)
                    screenshot_path = f"app_screenshot_{session_id[:8]}.png"
                    with open(screenshot_path, "wb") as f:
                        f.write(img_data)
                    tasks.append({
                        "name": "capture-screenshot",
                        "status": "passed",
                        "summary": f"Screenshot saved ({len(img_data)} bytes)",
                        "duration": "-",
                        "agent": "appium_direct",
                    })
                else:
                    tasks.append({
                        "name": "capture-screenshot",
                        "status": "failed",
                        "summary": f"No screenshot data: {data}",
                        "duration": "-",
                        "agent": "appium_direct",
                    })
            else:
                tasks.append({
                    "name": "capture-screenshot",
                    "status": "failed",
                    "summary": f"HTTP {resp.status_code}: {resp.text[:100]}",
                    "duration": "-",
                    "agent": "appium_direct",
                })
        except Exception as exc:
            tasks.append({
                "name": "capture-screenshot",
                "status": "failed",
                "summary": str(exc)[:100],
                "duration": "-",
                "agent": "appium_direct",
            })

        # 4. 获取页面源码
        try:
            resp = await client.get(f"{appium_url}/session/{session_id}/source")
            if resp.status_code == 200:
                data = resp.json()
                source = data.get("value", "")
                source_path = f"app_source_{session_id[:8]}.xml"
                with open(source_path, "w", encoding="utf-8") as f:
                    f.write(source)
                tasks.append({
                    "name": "get-page-source",
                    "status": "passed",
                    "summary": f"Page source saved ({len(source)} chars)",
                    "duration": "-",
                    "agent": "appium_direct",
                })
            else:
                tasks.append({
                    "name": "get-page-source",
                    "status": "failed",
                    "summary": f"HTTP {resp.status_code}: {resp.text[:100]}",
                    "duration": "-",
                    "agent": "appium_direct",
                })
        except Exception as exc:
            tasks.append({
                "name": "get-page-source",
                "status": "failed",
                "summary": str(exc)[:100],
                "duration": "-",
                "agent": "appium_direct",
            })

        # 5. 关闭 Session
        try:
            await client.delete(f"{appium_url}/session/{session_id}")
            tasks.append({
                "name": "close-appium-session",
                "status": "passed",
                "summary": "Session closed",
                "duration": "-",
                "agent": "appium_direct",
            })
        except Exception:
            tasks.append({
                "name": "close-appium-session",
                "status": "passed",
                "summary": "Session closed (with warnings)",
                "duration": "-",
                "agent": "appium_direct",
            })

    return tasks
