"""Public routes: anyone with a link, no login (plan §6.6)."""

from typing import Annotated

from fastapi import APIRouter, Header, Path
from fastapi.responses import Response, StreamingResponse

from app.api.deps import SessionDep
from app.db.session import get_session_factory
from app.live.hub import LiveMessage, hub
from app.live.sse import live_stream
from app.schemas.public import LiveOut, RecapOut
from app.services.public import PublicService, load_live_event

hub.set_loader(load_live_event)

_Token = Annotated[str, Path(min_length=10, max_length=64, description="Share token")]

public_router = APIRouter(prefix="/public", tags=["Public"])


@public_router.get("/live/{token}", response_model=LiveOut)
async def get_live(token: _Token, session: SessionDep) -> LiveOut:
    """The live table: payload, summary, last update and when the scorer was last online."""
    _, body = await PublicService(session).live(token)
    return body


@public_router.get("/live/{token}/events", response_model=None, response_class=StreamingResponse)
async def live_events(
    token: _Token,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """SSE: `game` on every upload (id = rev), `ping` every 25 s, `revoked` when switched off."""
    # Own short session: a stream can last an hour and must not hold a pooled connection.
    async with get_session_factory()() as session:
        game_id, body = await PublicService(session).live(token)
    rev = int(body.game.get("rev", 0))
    first = None
    if last_event_id is None or not last_event_id.isdigit() or int(last_event_id) < rev:
        first = LiveMessage(kind="game", rev=rev, data=body.model_dump_json(by_alias=True))
    queue = await hub.subscribe(game_id, token)
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
