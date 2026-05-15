from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from testagent.common import get_logger
from testagent.common.errors import TestAgentError

if TYPE_CHECKING:
    from fastapi import FastAPI, Request
    from starlette.responses import Response as StarletteResponse

_logger = get_logger(__name__)

_AUTH_TOKEN_HEADER = "Authorization"
_AUTH_TOKEN_PREFIX = "Bearer "


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, api_token: str | None = None) -> None:
        super().__init__(app)
        self._api_token = api_token

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> StarletteResponse:
        if self._api_token is None:
            return await call_next(request)

        if request.url.path.startswith("/health"):
            return await call_next(request)

        auth_header = request.headers.get(_AUTH_TOKEN_HEADER, "")
        if not auth_header.startswith(_AUTH_TOKEN_PREFIX):
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "MISSING_AUTH_TOKEN",
                        "message": "Missing or invalid Authorization header",
                    }
                },
            )

        token = auth_header[len(_AUTH_TOKEN_PREFIX) :]
        if token != self._api_token:
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "INVALID_AUTH_TOKEN",
                        "message": "Invalid API token",
                    }
                },
            )

        return await call_next(request)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, max_requests: int = 100, window_seconds: int = 60) -> None:
        super().__init__(app)
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._clients: dict[str, list[float]] = {}

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> StarletteResponse:
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        timestamps = self._clients.get(client_ip, [])
        timestamps = [t for t in timestamps if now - t < self._window_seconds]

        if len(timestamps) >= self._max_requests:
            retry_after = int(self._window_seconds - (now - timestamps[0]))
            return JSONResponse(
                status_code=429,
                content={
                    "error": {
                        "code": "RATE_LIMIT_EXCEEDED",
                        "message": f"Rate limit exceeded. Try again in {retry_after} seconds",
                    }
                },
                headers={"Retry-After": str(retry_after)},
            )

        timestamps.append(now)
        self._clients[client_ip] = timestamps

        return await call_next(request)


class ErrorHandlingMiddleware(BaseHTTPMiddleware):
    """Catches exceptions from downstream middleware and route handlers,
    returning structured JSON error responses.

    In Starlette 1.0.0, ServerErrorMiddleware always re-raises exceptions
    after handling them (line 184-186 of errors.py). This middleware runs
    inside ServerErrorMiddleware and catches exceptions before they reach
    the re-raise, allowing proper JSON error responses.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> StarletteResponse:
        try:
            return await call_next(request)
        except TestAgentError as exc:
            return JSONResponse(
                status_code=_error_status_code(exc),
                content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
            )
        except Exception as exc:
            _logger.error(
                "Unhandled exception",
                extra={
                    "extra_data": {
                        "path": str(request.url.path),
                        "method": request.method,
                        "error": str(exc),
                    }
                },
            )
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "An internal error occurred",
                    }
                },
            )


def _error_status_code(exc: TestAgentError) -> int:
    code = exc.code
    if code == "SESSION_NOT_FOUND":
        return 404
    if code == "INVALID_STATE_TRANSITION":
        return 409
    if code == "MISSING_AUTH_TOKEN":
        return 401
    if code == "INVALID_AUTH_TOKEN":
        return 403
    if code == "RATE_LIMIT_EXCEEDED":
        return 429
    if "NOT_FOUND" in code:
        return 404
    if "INVALID" in code or "FAILED" in code:
        return 400
    return 500


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(TestAgentError, _testagent_exception_handler)
    app.add_middleware(ErrorHandlingMiddleware)


async def _testagent_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, TestAgentError):
        return JSONResponse(
            status_code=_error_status_code(exc),
            content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
        )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An internal error occurred",
            }
        },
    )


def register_middleware(
    app: FastAPI,
    api_token: str | None = None,
    rate_limit_enabled: bool = True,
) -> None:
    if rate_limit_enabled:
        app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AuthMiddleware, api_token=api_token)
    register_error_handlers(app)
