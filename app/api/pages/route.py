"""`/r/{token}` and `/l/{token}`: preview pages for WhatsApp that redirect into the app."""

from typing import Annotated

from fastapi import APIRouter, Path
from fastapi.responses import HTMLResponse

from app.api.deps import SessionDep
from app.services.public import PublicService

_Token = Annotated[str, Path(min_length=1, max_length=64)]

pages_router = APIRouter(tags=["Pages"], include_in_schema=False)


@pages_router.get("/r/{token}", response_class=HTMLResponse)
async def recap_page(token: _Token, session: SessionDep) -> HTMLResponse:
    """Open Graph tags for a recap, then a redirect to /?recap=<token>."""
    status, page = await PublicService(session).preview_page("recap", token)
    return HTMLResponse(page, status_code=status, headers={"Cache-Control": "public, max-age=300"})


@pages_router.get("/l/{token}", response_class=HTMLResponse)
async def live_page(token: _Token, session: SessionDep) -> HTMLResponse:
    """Open Graph tags for a live table, then a redirect to /?live=<token>."""
    status, page = await PublicService(session).preview_page("live", token)
    return HTMLResponse(page, status_code=status, headers={"Cache-Control": "public, max-age=60"})
