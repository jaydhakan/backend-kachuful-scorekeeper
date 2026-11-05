"""ApiError and the handlers that turn every failure into `{code, message, ...extra}`."""

import json
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Send

from app.logger import get_logger

logger = get_logger(__name__)


class ErrorCode(StrEnum):
    """Every error code the API can return (plan §6)."""

    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    GONE = "gone"
    INVALID = "invalid"
    RATE_LIMITED = "rate_limited"
    CSRF = "csrf"
    BAD_CREDENTIALS = "bad_credentials"
    INACTIVE = "inactive"
    WEAK_PASSWORD = "weak_password"  # noqa: S105 - an error code, not a password
    NAME_TAKEN = "name_taken"
    EMAIL_TAKEN = "email_taken"
    SAME_PLAYER = "same_player"
    PLAYER_LINKED = "player_linked"
    USER_EXISTS = "user_exists"
    LAST_ADMIN = "last_admin"
    CANNOT_DELETE_ADMIN = "cannot_delete_admin"
    CANNOT_DELETE_SELF = "cannot_delete_self"
    BOTH_LINKED = "both_linked"
    ID_CONFLICT = "id_conflict"
    STALE = "stale"
    UNKNOWN_PLAYER = "unknown_player"
    INVALID_GAME = "invalid_game"
    TOO_LARGE = "too_large"
    UNSUPPORTED_MEDIA_TYPE = "unsupported_media_type"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    INTERNAL = "internal"


class ApiError(Exception):
    """An error for the client. `extra` keys are sent as-is, so pass them in camelCase."""

    def __init__(self, status: int, code: ErrorCode, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.extra = extra


def error_body(code: ErrorCode, message: str, **extra: Any) -> dict[str, Any]:
    """The JSON body every error uses."""
    return {"code": code.value, "message": message, **extra}


async def send_error(send: Send, status: int, code: ErrorCode, message: str) -> None:
    """Send an error response from raw ASGI middleware (outside FastAPI's handlers)."""
    raw = json.dumps(error_body(code, message)).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(raw)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": raw})


_STATUS_CODES = {
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    413: ErrorCode.TOO_LARGE,
    415: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
}


def _field_name(loc: tuple[int | str, ...]) -> str:
    parts = [str(part) for part in loc[1:]] if len(loc) > 1 else [str(part) for part in loc]
    return ".".join(parts)


async def _api_error_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return JSONResponse(error_body(exc.code, exc.message, **exc.extra), status_code=exc.status)


async def _validation_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    fields: dict[str, str] = {}
    for err in exc.errors():
        fields.setdefault(_field_name(tuple(err.get("loc", ()))), str(err.get("msg", "invalid")))
    body = error_body(ErrorCode.INVALID, "Some fields are not valid.", fields=fields)
    return JSONResponse(body, status_code=422)


async def _http_handler(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = _STATUS_CODES.get(exc.status_code, ErrorCode.INVALID)
    return JSONResponse(
        error_body(code, str(exc.detail)), status_code=exc.status_code, headers=exc.headers
    )


async def _unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled error",
        exc_info=exc,
        extra={"method": request.method, "route": _route_template(request)},
    )
    return JSONResponse(error_body(ErrorCode.INTERNAL, "Something went wrong."), status_code=500)


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    return str(getattr(route, "path", "?"))


def register_exception_handlers(app: FastAPI) -> None:
    """Install the one error format on the app."""
    app.add_exception_handler(ApiError, _api_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_handler)
    app.add_exception_handler(StarletteHTTPException, _http_handler)
    app.add_exception_handler(Exception, _unhandled_handler)
