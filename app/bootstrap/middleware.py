"""Pure ASGI middleware (BaseHTTPMiddleware would buffer SSE streams)."""

import re
import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.exceptions import ErrorCode, send_error
from app.logger import get_logger

logger = get_logger("app.access")

MAX_BODY_BYTES = 1024 * 1024
# Player photos may be up to 2 MB before they are re-encoded.
PHOTO_BODY_BYTES = 2 * 1024 * 1024
_PHOTO_PATH = re.compile(r"^/api/players/[^/]+/photo$")


def body_limit(path: str) -> int:
    """The largest request body accepted on a path."""
    return PHOTO_BODY_BYTES if _PHOTO_PATH.match(path) else MAX_BODY_BYTES


class BodyTooLargeError(Exception):
    """Raised mid-stream when a chunked body passes the limit."""


class BodySizeLimitMiddleware:
    """Refuse request bodies over 1 MB with `413 too_large` (nginx enforces the same)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Check Content-Length up front and count chunked bodies as they arrive."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = body_limit(scope["path"])
        length = dict(scope["headers"]).get(b"content-length")
        if length is not None and length.isdigit() and int(length) > limit:
            await send_error(send, 413, ErrorCode.TOO_LARGE, "Request body too large.")
            return
        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > limit:
                raise BodyTooLargeError
            return message

        try:
            await self.app(scope, counting_receive, send)
        except BodyTooLargeError:
            await send_error(send, 413, ErrorCode.TOO_LARGE, "Request body too large.")


class AccessLogMiddleware:
    """One structured line per request: method, route template (never the raw path), status, ms."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Time the request and log it once the response starts."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = 0

        async def capture_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture_send)
        finally:
            route = scope.get("route")
            logger.info(
                "request",
                extra={
                    "method": scope["method"],
                    "route": getattr(route, "path", "unmatched"),
                    "status": status,
                    "ms": round((time.perf_counter() - started) * 1000, 1),
                },
            )
