"""Turning a Cached result into a 200 with ETag or a bodiless 304."""

from typing import Annotated

from fastapi import Header
from starlette.responses import Response

from app.services.cache import Cached

IfNoneMatch = Annotated[str | None, Header(alias="If-None-Match")]

IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "private, no-cache"


def respond[T](cached: Cached[T], response: Response, *, immutable: bool = False) -> T | Response:
    """The body with its ETag, or 304 Not Modified when the client already has it."""
    headers = {"ETag": cached.etag, "Cache-Control": IMMUTABLE if immutable else REVALIDATE}
    if cached.body is None:
        return Response(status_code=304, headers=headers)
    response.headers.update(headers)
    return cached.body
