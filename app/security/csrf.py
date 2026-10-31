"""CSRF guard: every non-GET under /api needs `X-Kachuful: 1` and our own Origin."""

from starlette.types import ASGIApp, Receive, Scope, Send

from app.exceptions import ErrorCode, send_error
from app.settings import settings

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_ORIGIN = settings.APP_ORIGIN.encode()


class CsrfMiddleware:
    """Reject unsafe requests that a browser on another site could have sent."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """403 `csrf` unless both the custom header and the Origin are right."""
        if (
            scope["type"] == "http"
            and scope["method"] not in _SAFE_METHODS
            and scope["path"].startswith("/api/")
        ):
            headers = dict(scope["headers"])
            if headers.get(b"x-kachuful") != b"1" or headers.get(b"origin") != _ORIGIN:
                await send_error(send, 403, ErrorCode.CSRF, "Missing or foreign request origin.")
                return
        await self.app(scope, receive, send)
