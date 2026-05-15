from __future__ import annotations

import contextlib
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from testagent.common.errors import MCPServerUnavailableError, TestAgentError
from testagent.gateway.app import create_app
from testagent.gateway.middleware import (
    AuthMiddleware,
    RateLimitMiddleware,
    register_error_handlers,
)
from testagent.gateway.router import router, set_mcp_registry, set_session_manager
from testagent.gateway.session import SessionManager, SessionNotFoundError, SessionStateError
from testagent.gateway.websocket import SessionWebSocketManager

# =============================================================================
# SessionManager Tests
# =============================================================================


class TestSessionManager:
    @pytest.fixture()
    def mgr(self) -> SessionManager:
        return SessionManager()

    async def test_create_session(self, mgr: SessionManager) -> None:
        session = await mgr.create_session(
            name="test-session",
            trigger_type="manual",
            input_context={"url": "https://example.com"},
        )
        assert session["name"] == "test-session"
        assert session["status"] == "pending"
        assert session["trigger_type"] == "manual"
        assert session["input_context"] == {"url": "https://example.com"}
        assert "id" in session
        assert "created_at" in session
        assert session["completed_at"] is None

    async def test_create_session_defaults(self, mgr: SessionManager) -> None:
        session = await mgr.create_session(name="default-test")
        assert session["status"] == "pending"
        assert session["trigger_type"] == "manual"
        assert session["input_context"] == {}

    async def test_get_session_found(self, mgr: SessionManager) -> None:
        created = await mgr.create_session(name="find-me")
        fetched = await mgr.get_session(created["id"])
        assert fetched["id"] == created["id"]
        assert fetched["name"] == "find-me"

    async def test_get_session_not_found(self, mgr: SessionManager) -> None:
        with pytest.raises(SessionNotFoundError) as exc:
            await mgr.get_session("nonexistent-id")
        assert exc.value.code == "SESSION_NOT_FOUND"

    async def test_list_sessions(self, mgr: SessionManager) -> None:
        await mgr.create_session(name="s1")
        await mgr.create_session(name="s2")
        sessions = await mgr.list_sessions()
        assert len(sessions) == 2

    async def test_transition_valid(self, mgr: SessionManager) -> None:
        session = await mgr.create_session(name="transitions")
        assert session["status"] == "pending"

        session = await mgr.transition(session["id"], "planning")
        assert session["status"] == "planning"

        session = await mgr.transition(session["id"], "executing")
        assert session["status"] == "executing"

        session = await mgr.transition(session["id"], "analyzing")
        assert session["status"] == "analyzing"

        session = await mgr.transition(session["id"], "completed")
        assert session["status"] == "completed"
        assert session["completed_at"] is not None

    async def test_transition_invalid(self, mgr: SessionManager) -> None:
        session = await mgr.create_session(name="invalid-trans")
        with pytest.raises(SessionStateError) as exc:
            await mgr.transition(session["id"], "completed")
        assert exc.value.code == "INVALID_STATE_TRANSITION"
        assert "pending" in str(exc.value.details["current_status"])
        assert "completed" in str(exc.value.details["requested_status"])

    async def test_transition_not_found(self, mgr: SessionManager) -> None:
        with pytest.raises(SessionNotFoundError):
            await mgr.transition("no-such-id", "planning")

    async def test_cancel_session(self, mgr: SessionManager) -> None:
        session = await mgr.create_session(name="cancel-me")
        cancelled = await mgr.cancel_session(session["id"])
        assert cancelled["status"] == "failed"
        assert cancelled["completed_at"] is not None

    async def test_subscribe_events(self, mgr: SessionManager) -> None:
        import asyncio

        session = await mgr.create_session(name="subscribe-test")

        collected: list[dict[str, Any]] = []

        async def collector() -> None:
            async for event in mgr.subscribe(session["id"]):
                collected.append(event)

        collector_task = asyncio.create_task(collector())
        await asyncio.sleep(0.02)

        await mgr.transition(session["id"], "planning")
        await mgr.publish_event(session["id"], "plan.generated", {"plan_id": "p1"})
        await mgr.transition(session["id"], "executing")
        await mgr.transition(session["id"], "analyzing")
        await mgr.transition(session["id"], "completed")

        await asyncio.sleep(0.05)
        collector_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await collector_task

        assert len(collected) >= 2
        events_found = {e["event"] for e in collected}
        assert "session.planning" in events_found
        assert "plan.generated" in events_found
        assert "session.completed" in events_found

    async def test_subscribe_stops_on_completed(self, mgr: SessionManager) -> None:

        session = await mgr.create_session(name="auto-stop")
        await mgr.transition(session["id"], "planning")
        await mgr.transition(session["id"], "executing")
        await mgr.transition(session["id"], "analyzing")
        await mgr.transition(session["id"], "completed")

        collected: list[dict[str, Any]] = []
        async for event in mgr.subscribe(session["id"]):
            collected.append(event)

        assert len(collected) == 1
        assert collected[0]["event"] == "session.completed"

    async def test_publish_event_unknown(self, mgr: SessionManager) -> None:
        session = await mgr.create_session(name="unknown-event")
        await mgr.publish_event(session["id"], "unknown.event", {})

    async def test_transition_to_failed(self, mgr: SessionManager) -> None:
        session = await mgr.create_session(name="fail-test")
        session = await mgr.transition(session["id"], "failed")
        assert session["status"] == "failed"
        assert session["completed_at"] is not None

    async def test_transition_from_completed_fails(self, mgr: SessionManager) -> None:
        session = await mgr.create_session(name="completed-blocked")
        await mgr.transition(session["id"], "planning")
        await mgr.transition(session["id"], "executing")
        await mgr.transition(session["id"], "analyzing")
        await mgr.transition(session["id"], "completed")
        with pytest.raises(SessionStateError):
            await mgr.transition(session["id"], "planning")

    async def test_transition_from_failed_fails(self, mgr: SessionManager) -> None:
        session = await mgr.create_session(name="failed-blocked")
        await mgr.transition(session["id"], "failed")
        with pytest.raises(SessionStateError):
            await mgr.transition(session["id"], "planning")


# =============================================================================
# Middleware Tests
# =============================================================================


class TestAuthMiddleware:
    @pytest.fixture()
    def app(self) -> FastAPI:
        app = FastAPI()
        app.add_middleware(AuthMiddleware, api_token="test-token-123")

        @app.get("/test")
        async def test_endpoint() -> dict[str, str]:
            return {"status": "ok"}

        @app.get("/health")
        async def health() -> dict[str, str]:
            return {"status": "healthy"}

        return app

    async def test_no_auth_header(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/test")
            assert resp.status_code == 401
            body = resp.json()
            assert body["error"]["code"] == "MISSING_AUTH_TOKEN"

    async def test_invalid_token(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/test", headers={"Authorization": "Bearer wrong-token"})
            assert resp.status_code == 403
            body = resp.json()
            assert body["error"]["code"] == "INVALID_AUTH_TOKEN"

    async def test_valid_token(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/test", headers={"Authorization": "Bearer test-token-123"})
            assert resp.status_code == 200
            assert resp.json() == {"status": "ok"}

    async def test_health_skips_auth(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200

    async def test_no_auth_when_token_none(self) -> None:
        app = FastAPI()
        app.add_middleware(AuthMiddleware, api_token=None)

        @app.get("/test")
        async def test_endpoint() -> dict[str, str]:
            return {"status": "ok"}

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/test")
            assert resp.status_code == 200

    async def test_no_bearer_prefix(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/test", headers={"Authorization": "test-token-123"})
            assert resp.status_code == 401


class TestRateLimitMiddleware:
    @pytest.fixture()
    def app(self) -> FastAPI:
        app = FastAPI()
        app.add_middleware(RateLimitMiddleware, max_requests=3, window_seconds=60)

        @app.get("/test")
        async def test_endpoint() -> dict[str, str]:
            return {"status": "ok"}

        return app

    async def test_allows_requests_within_limit(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(3):
                resp = await client.get("/test")
                assert resp.status_code == 200

    async def test_blocks_requests_exceeding_limit(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(3):
                await client.get("/test")

            resp = await client.get("/test")
            assert resp.status_code == 429
            body = resp.json()
            assert body["error"]["code"] == "RATE_LIMIT_EXCEEDED"
            assert "Retry-After" in resp.headers


class TestErrorHandlers:
    @pytest.fixture()
    def app(self) -> FastAPI:
        app = FastAPI()
        register_error_handlers(app)

        @app.get("/testagent-error")
        async def raise_testagent_error() -> None:
            raise TestAgentError(
                message="Something went wrong",
                code="TEST_ERROR",
                details={"key": "value"},
            )

        @app.get("/session-not-found")
        async def raise_session_not_found() -> None:
            raise TestAgentError(
                message="Session not found",
                code="SESSION_NOT_FOUND",
            )

        @app.get("/invalid-state")
        async def raise_invalid_state() -> None:
            raise TestAgentError(
                message="Invalid transition",
                code="INVALID_STATE_TRANSITION",
            )

        @app.get("/rate-limited")
        async def raise_rate_limited() -> None:
            raise TestAgentError(
                message="Rate limited",
                code="RATE_LIMIT_EXCEEDED",
            )

        @app.get("/generic-error")
        async def raise_generic_error() -> None:
            msg = "Internal failure"
            raise RuntimeError(msg)

        return app

    async def test_testagent_error_response(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/testagent-error")
            assert resp.status_code == 500
            body = resp.json()
            assert body["error"]["code"] == "TEST_ERROR"
            assert body["error"]["message"] == "Something went wrong"
            assert body["error"]["details"] == {"key": "value"}

    async def test_session_not_found_maps_to_404(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/session-not-found")
            assert resp.status_code == 404
            assert resp.json()["error"]["code"] == "SESSION_NOT_FOUND"

    async def test_invalid_state_maps_to_409(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/invalid-state")
            assert resp.status_code == 409
            assert resp.json()["error"]["code"] == "INVALID_STATE_TRANSITION"

    async def test_rate_limited_maps_to_429(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/rate-limited")
            assert resp.status_code == 429
            assert resp.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"

    async def test_generic_error_response(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/generic-error")
            assert resp.status_code == 500
            body = resp.json()
            assert body["error"]["code"] == "INTERNAL_ERROR"


# =============================================================================
# REST API Router Tests
# =============================================================================


class TestRouterSessionEndpoints:
    @pytest.fixture()
    def app(self) -> FastAPI:
        app = FastAPI()
        register_error_handlers(app)
        mgr = SessionManager()
        set_session_manager(mgr)
        app.include_router(router)
        return app

    async def test_create_session(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/sessions",
                json={"name": "test-session", "trigger_type": "manual", "input_context": {"key": "val"}},
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body["data"]["name"] == "test-session"
            assert body["data"]["status"] == "pending"
            assert body["data"]["trigger_type"] == "manual"
            assert body["data"]["input_context"] == {"key": "val"}

    async def test_create_session_defaults(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/sessions",
                json={"name": "minimal-session"},
            )
            assert resp.status_code == 201
            body = resp.json()
            assert body["data"]["name"] == "minimal-session"
            assert body["data"]["status"] == "pending"
            assert body["data"]["trigger_type"] == "manual"

    async def test_get_session(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            create_resp = await client.post("/api/v1/sessions", json={"name": "get-me"})
            session_id = create_resp.json()["data"]["id"]

            resp = await client.get(f"/api/v1/sessions/{session_id}")
            assert resp.status_code == 200
            assert resp.json()["data"]["id"] == session_id

    async def test_get_session_not_found(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/sessions/nonexistent")
            assert resp.status_code == 404

    async def test_get_session_plans(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            create_resp = await client.post("/api/v1/sessions", json={"name": "plans-test"})
            session_id = create_resp.json()["data"]["id"]

            resp = await client.get(f"/api/v1/sessions/{session_id}/plans")
            assert resp.status_code == 200
            assert resp.json()["data"] == []

    async def test_get_session_results(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            create_resp = await client.post("/api/v1/sessions", json={"name": "results-test"})
            session_id = create_resp.json()["data"]["id"]

            resp = await client.get(f"/api/v1/sessions/{session_id}/results")
            assert resp.status_code == 200
            assert resp.json()["data"] == []

    async def test_cancel_session(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            create_resp = await client.post("/api/v1/sessions", json={"name": "cancel-test"})
            session_id = create_resp.json()["data"]["id"]

            resp = await client.post(f"/api/v1/sessions/{session_id}/cancel")
            assert resp.status_code == 200
            assert resp.json()["data"]["status"] == "failed"

    async def test_cancel_session_not_found(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/sessions/nonexistent/cancel")
            assert resp.status_code == 404

    async def test_get_report(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            create_resp = await client.post("/api/v1/sessions", json={"name": "report-test"})
            session_id = create_resp.json()["data"]["id"]

            resp = await client.get(f"/api/v1/reports/{session_id}")
            assert resp.status_code == 200
            body = resp.json()
            assert body["data"]["session"]["id"] == session_id
            assert "summary" in body["data"]


class TestRouterSkillEndpoints:
    @pytest.fixture()
    def app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(router)
        return app

    async def test_list_skills_empty(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/skills")
            assert resp.status_code == 200
            assert resp.json()["data"] == []

    async def test_get_skill_not_found(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/skills/nonexistent_skill")
            assert resp.status_code == 404


class TestRouterMCPEndpoints:
    @pytest.fixture()
    def mock_registry(self) -> MagicMock:
        registry = MagicMock()
        registry.list_servers = AsyncMock(return_value=[])
        registry.lookup = AsyncMock(
            side_effect=MCPServerUnavailableError(message="Not found", code="MCP_SERVER_NOT_FOUND")
        )
        registry.register = AsyncMock()
        return registry

    @pytest.fixture()
    def app(self, mock_registry: MagicMock) -> FastAPI:
        set_mcp_registry(mock_registry)
        app = FastAPI()
        app.include_router(router)
        return app

    async def test_list_mcp_servers(self, app: FastAPI, mock_registry: MagicMock) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/mcp/servers")
            assert resp.status_code == 200
            assert resp.json()["data"] == []
            mock_registry.list_servers.assert_awaited_once()

    async def test_get_mcp_health_not_found(self, app: FastAPI, mock_registry: MagicMock) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/mcp/servers/unknown/health")
            assert resp.status_code == 404


class TestRouterRAGEndpoints:
    @pytest.fixture()
    def app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(router)
        return app

    async def test_rag_index(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/rag/index",
                json={"source": "./docs", "collection": "req_docs"},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["data"]["status"] == "queued"
            assert body["data"]["source"] == "./docs"

    async def test_rag_query(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/rag/query",
                json={"query": "test query", "collection": "api_docs", "top_k": 3},
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["data"]["query"] == "test query"
            assert body["data"]["total"] == 0

    async def test_rag_query_defaults(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/rag/query",
                json={"query": "default test"},
            )
            assert resp.status_code == 200
            assert resp.json()["data"]["collection"] == "req_docs"
            assert resp.json()["data"]["total"] == 0


class TestRouterHealthEndpoint:
    @pytest.fixture()
    def app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(router)
        return app

    async def test_health(self, app: FastAPI) -> None:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            assert resp.json() == {"status": "healthy"}


# =============================================================================
# WebSocket Tests
# =============================================================================


class TestSessionWebSocketManager:
    @pytest.fixture()
    def session_manager(self) -> SessionManager:
        return SessionManager()

    @pytest.fixture()
    def ws_manager(self, session_manager: SessionManager) -> SessionWebSocketManager:
        return SessionWebSocketManager(session_manager)

    async def test_handle_websocket_session_not_found(self, ws_manager: SessionWebSocketManager) -> None:
        mock_ws = AsyncMock()
        await ws_manager.handle_websocket(mock_ws, "nonexistent")
        mock_ws.accept.assert_awaited_once()
        mock_ws.send_json.assert_awaited_once()
        sent = mock_ws.send_json.call_args[0][0]
        assert sent["event"] == "error"
        assert sent["data"]["code"] == "SESSION_NOT_FOUND"
        mock_ws.close.assert_awaited_once_with(code=4004)

    async def test_handle_websocket_receives_events(
        self, session_manager: SessionManager, ws_manager: SessionWebSocketManager
    ) -> None:
        session = await session_manager.create_session(name="ws-test")
        mock_ws = AsyncMock()

        async def mock_receive_json() -> dict[str, object]:
            raise __import__("fastapi").WebSocketDisconnect()

        mock_ws.receive_json = mock_receive_json

        await ws_manager.handle_websocket(mock_ws, session["id"])

        mock_ws.accept.assert_awaited_once()

    async def test_client_cancel_message(
        self, session_manager: SessionManager, ws_manager: SessionWebSocketManager
    ) -> None:
        session = await session_manager.create_session(name="ws-cancel")
        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(
            side_effect=[
                {"event": "session.cancel", "data": {}},
                __import__("fastapi").WebSocketDisconnect(),
            ]
        )

        await ws_manager.handle_websocket(mock_ws, session["id"])
        mock_ws.accept.assert_awaited_once()

        updated = await session_manager.get_session(session["id"])
        assert updated["status"] == "failed"

    async def test_unknown_client_event(
        self, session_manager: SessionManager, ws_manager: SessionWebSocketManager
    ) -> None:
        session = await session_manager.create_session(name="ws-unknown")
        mock_ws = AsyncMock()
        mock_ws.receive_json = AsyncMock(
            side_effect=[
                {"event": "unknown.event", "data": {}},
                __import__("fastapi").WebSocketDisconnect(),
            ]
        )

        await ws_manager.handle_websocket(mock_ws, session["id"])
        mock_ws.accept.assert_awaited_once()


# =============================================================================
# Integration: Full App Tests
# =============================================================================


class TestCreateApp:
    def test_create_app_returns_fastapi(self) -> None:
        app = create_app()
        assert isinstance(app, FastAPI)
        assert app.title == "TestAgent"
        assert app.version == "0.1.0"

    def test_create_app_has_routes(self) -> None:
        app = create_app()
        routes = [r.path for r in app.routes]  # type: ignore[attr-defined]
        assert "/api/v1/sessions" in routes
        assert "/api/v1/sessions/{session_id}" in routes
        assert "/api/v1/sessions/{session_id}/plans" in routes
        assert "/api/v1/sessions/{session_id}/results" in routes
        assert "/api/v1/sessions/{session_id}/cancel" in routes
        assert "/api/v1/skills" in routes
        assert "/api/v1/skills/{skill_name}" in routes
        assert "/api/v1/mcp/servers" in routes
        assert "/api/v1/mcp/servers/{server_name}/health" in routes
        assert "/api/v1/rag/index" in routes
        assert "/api/v1/rag/query" in routes
        assert "/api/v1/reports/{session_id}" in routes
        assert "/api/v1/ws/sessions/{session_id}" in routes
        assert "/health" in routes

    async def test_app_create_session_end_to_end(self) -> None:
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/sessions",
                json={"name": "e2e-test", "trigger_type": "manual"},
            )
            assert resp.status_code == 201
            session_id = resp.json()["data"]["id"]

            get_resp = await client.get(f"/api/v1/sessions/{session_id}")
            assert get_resp.status_code == 200
            assert get_resp.json()["data"]["name"] == "e2e-test"

            cancel_resp = await client.post(f"/api/v1/sessions/{session_id}/cancel")
            assert cancel_resp.status_code == 200
            assert cancel_resp.json()["data"]["status"] == "failed"

    async def test_app_health_check(self) -> None:
        app = create_app()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
