"""App-wide appearance (contract change 2026-11-01). Public read; admins write it under /admin."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response

from app.api.deps import get_appearance_service
from app.api.etag import IfNoneMatch, respond
from app.schemas.appearance import AppearanceOut
from app.services.appearance import AppearanceService

_AppearanceServiceDep = Annotated[AppearanceService, Depends(get_appearance_service)]

# Signed-out devices read it too; clients re-check on focus and every 5 minutes.
_ONE_MINUTE = "public, max-age=60"

appearance_router = APIRouter(prefix="/settings", tags=["Settings"])


@appearance_router.get("/appearance", response_model=AppearanceOut)
async def get_appearance(
    service: _AppearanceServiceDep, response: Response, if_none_match: IfNoneMatch = None
) -> AppearanceOut | Response:
    """The theme everyone sees and whether players may choose their own (signed out allowed)."""
    return respond(await service.get(if_none_match), response, cache_control=_ONE_MINUTE)
