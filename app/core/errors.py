from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

import logging

log = logging.getLogger("app.errors")


class ApiError(StarletteHTTPException):
    """Error de negocio con código opcional (p. ej. ACCOUNT_LOCKED)."""

    def __init__(self, status_code: int, message: str, code: str | None = None, headers: dict[str, str] | None = None):
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.message = message
        self.code = code


def bad_request(message: str) -> ApiError:
    return ApiError(400, message)


def unauthorized(message: str = "Invalid or expired token", code: str | None = None) -> ApiError:
    return ApiError(401, message, code, headers={"WWW-Authenticate": "Bearer"})


def forbidden(message: str = "Insufficient permissions") -> ApiError:
    return ApiError(403, message)


def not_found(message: str) -> ApiError:
    return ApiError(404, message)


def conflict(message: str) -> ApiError:
    return ApiError(409, message)


def _body(status: int, message: Any, request: Request, code: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "statusCode": status,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "path": request.url.path,
    }
    if code:
        body["code"] = code
    return body


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        code = getattr(exc, "code", None)
        message = getattr(exc, "message", None) or (exc.detail if isinstance(exc.detail, str) else "Request failed")
        return JSONResponse(_body(exc.status_code, message, request, code), status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        messages = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"] if p not in ("body", "query", "path", "form"))
            messages.append(f"{loc}: {err['msg']}" if loc else err["msg"])
        return JSONResponse(_body(400, messages, request), status_code=400)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("Error no controlado en %s %s", request.method, request.url.path)
        return JSONResponse(_body(500, "Internal server error", request), status_code=500)
