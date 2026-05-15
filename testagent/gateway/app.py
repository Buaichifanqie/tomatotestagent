from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, WebSocket

from testagent.common import get_logger
from testagent.config.settings import get_settings
from testagent.gateway.middleware import register_error_handlers, register_middleware
from testagent.gateway.router import router, set_mcp_registry, set_session_manager
from testagent.gateway.session import SessionManager
from testagent.gateway.websocket import SessionWebSocketManager

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_logger = get_logger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()

    session_manager = SessionManager()
    set_session_manager(session_manager)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        _logger.info("Gateway starting up", extra={"extra_data": {"version": settings.app_version}})

        try:
            from testagent.gateway.mcp_registry import MCPRegistry

            registry = MCPRegistry(settings)
            await registry.start_health_monitor()
            app.state.mcp_registry = registry
            set_mcp_registry(registry)
            _logger.info("MCP Registry initialized with health monitor")
        except Exception as exc:
            _logger.warning(
                "MCP Registry initialization skipped",
                extra={"extra_data": {"error": str(exc)}},
            )
            app.state.mcp_registry = None

        app.state.session_manager = session_manager
        app.state.ws_manager = SessionWebSocketManager(session_manager)

        yield

        _logger.info("Gateway shutting down")
        shutdown_registry: object = getattr(app.state, "mcp_registry", None)
        if shutdown_registry is not None:
            from testagent.gateway.mcp_registry import MCPRegistry

            assert isinstance(shutdown_registry, MCPRegistry)
            await shutdown_registry.stop_health_monitor()
            _logger.info("Health monitor stopped")

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    app.include_router(router)

    api_token = _resolve_api_token(settings)
    register_middleware(app, api_token=api_token)
    register_error_handlers(app)

    @app.websocket("/api/v1/ws/sessions/{session_id}")
    async def session_websocket(websocket: WebSocket, session_id: str) -> None:
        ws_manager: SessionWebSocketManager | None = getattr(app.state, "ws_manager", None)
        if ws_manager is None:
            await websocket.accept()
            await websocket.send_json(
                {
                    "event_type": "error",
                    "session_id": session_id,
                    "data": {"code": "WS_UNAVAILABLE", "message": "WebSocket manager not available"},
                }
            )
            await websocket.close()
            return
        await ws_manager.handle_websocket(websocket, session_id)

    @app.websocket("/api/v1/ws")
    async def global_websocket(websocket: WebSocket) -> None:
        mgr: SessionManager | None = getattr(app.state, "session_manager", None)
        if mgr is None:
            await websocket.accept()
            await websocket.send_json(
                {
                    "event_type": "error",
                    "data": {"code": "SESSION_MANAGER_UNAVAILABLE", "message": "Session manager not available"},
                }
            )
            await websocket.close()
            return
        await websocket.accept()
        try:
            import asyncio
            from datetime import UTC, datetime

            q = await mgr.subscribe_global()
            try:
                while True:
                    try:
                        message = await asyncio.wait_for(q.get(), timeout=30.0)
                        message["timestamp"] = datetime.now(UTC).isoformat()
                        await websocket.send_json(message)
                    except asyncio.TimeoutError:
                        try:
                            await websocket.send_json({"event_type": "ping"})
                        except Exception:
                            break
            finally:
                await mgr.unsubscribe_global(q)
        except Exception as exc:
            _logger.warning(
                "Global WebSocket connection error",
                extra={"extra_data": {"error": str(exc)}},
            )

    return app


def _resolve_api_token(settings: Any) -> str | None:
    return os.environ.get("TESTAGENT_API_TOKEN") or os.environ.get("TESTAGENT_GATEWAY_TOKEN")


app = create_app()
