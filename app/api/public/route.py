"""Public routes: anyone with a link, no login (plan §6.6)."""

from typing import Annotated

from fastapi import APIRouter, Header, Path, Query, Request
from fastapi.responses import Response, StreamingResponse

from app.api.deps import OptionalAuth, SessionDep, client_ip
from app.db.session import get_session_factory
from app.live.hub import LiveMessage, hub
from app.live.sse import live_stream
from app.schemas.public import LiveOut, ReactionIn, RecapOut
from app.services.public import PublicService, load_live_event

hub.set_loader(load_live_event)

_Token = Annotated[str, Path(min_length=10, max_length=64, description="Share token")]

public_router = APIRouter(prefix="/public", tags=["Public"])


@public_router.get("/live/{token}", response_model=LiveOut)
async def get_live(token: _Token, session: SessionDep, ctx: OptionalAuth) -> LiveOut:
    """The live table: payload, summary, last update and when the scorer was last online."""
    _, body = await PublicService(session).live(token, signed_in=ctx is not None)
    return body


@public_router.post("/live/{token}/reactions", status_code=204)
async def react(
    token: _Token, body: ReactionIn, request: Request, session: SessionDep, ctx: OptionalAuth
) -> None:
    """A spectator's reaction (not stored): every viewer and the scorer get an SSE `reaction`."""
    await PublicService(session).react(token, body.kind, client_ip(request), ctx is not None)


@public_router.get("/live/{token}/events", response_model=None, response_class=StreamingResponse)
async def live_events(
    token: _Token,
    ctx: OptionalAuth,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    host: Annotated[bool, Query(description="The scorer's own phone: not counted")] = False,
) -> StreamingResponse:
    """SSE: `game` on every upload (id = rev), `reaction`, `watchers`, `ping` every 25 s, `revoked`."""
    # Own short session: a stream can last an hour and must not hold a pooled connection.
    async with get_session_factory()() as session:
        game_id, body = await PublicService(session).live(token, signed_in=ctx is not None)
    rev = int(body.game.get("rev", 0))
    first = None
    if last_event_id is None or not last_event_id.isdigit() or int(last_event_id) < rev:
        first = LiveMessage(kind="game", rev=rev, data=body.model_dump_json(by_alias=True))
    queue = await hub.subscribe(game_id, token, counted=not host)
    return StreamingResponse(
        live_stream(game_id, queue, first),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@public_router.get("/recap/{token}", response_model=RecapOut)
async def get_recap(token: _Token, session: SessionDep) -> RecapOut:
    """A shareable recap: payload, summary and the game's achievements."""
    return await PublicService(session).recap(token)


@public_router.get("/recap/{token}/og.png", response_model=None, response_class=Response)
async def get_recap_image(token: _Token, session: SessionDep) -> Response:
    """The 1200x630 preview image (cached on disk per token and rev)."""
    png = await PublicService(session).og_png(token)
    return Response(png, media_type="image/png", headers={"Cache-Control": "public, max-age=300"})
