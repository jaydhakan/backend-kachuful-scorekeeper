"""Spades online tables: the server runs the game; each viewer gets only their own hand."""

import uuid
from functools import partial
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Path, Query, Response
from fastapi.responses import StreamingResponse

from app.api.deps import CurrentAuth, get_spades_tables_service
from app.db.session import get_session_factory
from app.live.hub import SPADES, GameKindHooks, hub
from app.live.table_stream import table_stream
from app.schemas.seat_invites import SeatInviteIn, SeatInviteOut
from app.schemas.spades_tables import (
    CreateTableIn,
    JoinTableIn,
    SpActionIn,
    SpTablesPageOut,
    SpViewOut,
    TableRefOut,
    TableRulesIn,
)
from app.security import rate_limit
from app.services.pagination import DEFAULT_LIMIT, MAX_LIMIT
from app.services.spades_store import frame_for, load_bundle, presence_frame, stamp_table_seen
from app.services.spades_tables import SpadesTablesService

hub.register_game_kind(
    SPADES,
    GameKindHooks(
        loader=load_bundle,
        frame_for=frame_for,
        events=("reaction", "ended"),
        presence_frame=presence_frame,
    ),
)

_TablesDep = Annotated[SpadesTablesService, Depends(get_spades_tables_service)]
_TableId = Annotated[uuid.UUID, Path(description="Table id")]

spades_tables_router = APIRouter(prefix="/tables")


@spades_tables_router.post("", response_model=SpViewOut, status_code=201)
async def create_spades_table(
    body: CreateTableIn, ctx: CurrentAuth, service: _TablesDep, response: Response
) -> SpViewOut:
    """Open a table: the creator takes seat 0 and hosts; it gets a 4-letter join code.

    The same host re-sending the same id gets the table with 200. `409 no_account` without a
    linked player; `409 id_conflict` for an id in use; `422 invalid` for bad rules or name.
    """
    await rate_limit.enforce(rate_limit.SPADES_CREATE, f"user:{ctx.user.id}")
    out, created = await service.create(body, ctx.user)
    if not created:
        response.status_code = 200
    return out


@spades_tables_router.get("", response_model=SpTablesPageOut)
async def list_spades_tables(
    ctx: CurrentAuth,
    service: _TablesDep,
    status: Annotated[
        Literal["active", "finished"] | None,
        Query(description="`active`: lobby or playing; `finished`: finished or cancelled"),
    ] = None,
    cursor: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> SpTablesPageOut:
    """Tables I sit at (or sat at before a Replace), newest change first. Summaries only."""
    return await service.list_page(ctx.user, status=status, cursor=cursor, limit=limit)


@spades_tables_router.get("/by-code/{code}", response_model=TableRefOut)
async def find_spades_table(
    code: Annotated[str, Path(min_length=4, max_length=4, description="Join code")],
    _: CurrentAuth,
    service: _TablesDep,
) -> TableRefOut:
    """The open table with this code (case-insensitive); 404 when none is open."""
    return await service.by_code(code)


@spades_tables_router.get("/{game_id}", response_model=SpViewOut)
async def get_spades_table(game_id: _TableId, ctx: CurrentAuth, service: _TablesDep) -> SpViewOut:
    """The table as I may see it: my own hand only, once I may see it."""
    return await service.get(game_id, ctx.user)


@spades_tables_router.post("/{game_id}/join", response_model=SpViewOut)
async def join_spades_table(
    game_id: _TableId, body: JoinTableIn, ctx: CurrentAuth, service: _TablesDep
) -> SpViewOut:
    """Take a free seat (the lowest if none named) or move within the lobby; or the seat a
    Replace opened. `409 full`, `409 seat_taken`, `409 no_account`, `422 invalid_action`."""
    return await service.join(game_id, body, ctx.user)


@spades_tables_router.patch("/{game_id}/rules", response_model=SpViewOut)
async def set_spades_rules(
    game_id: _TableId, body: TableRulesIn, ctx: CurrentAuth, service: _TablesDep
) -> SpViewOut:
    """Change the rules and name (host, lobby). `403 not_your_turn`, `422 invalid_action`."""
    return await service.set_rules(game_id, body, ctx.user)


@spades_tables_router.post("/{game_id}/actions", response_model=SpViewOut)
async def act_at_spades_table(
    game_id: _TableId, body: SpActionIn, ctx: CurrentAuth, service: _TablesDep
) -> SpViewOut:
    """Apply one action. A known `actionId` returns the view; `timeout` on a settled turn is a
    no-op. `409 stale`, `403 not_your_turn` and `422 invalid_action {reason}` carry `game`."""
    await rate_limit.enforce(rate_limit.SPADES_ACTION, f"game:{game_id}")
    return await service.act(game_id, body, ctx.user)


@spades_tables_router.post("/{game_id}/seat-invites", response_model=SeatInviteOut, status_code=201)
async def invite_to_spades_table(
    game_id: _TableId, body: SeatInviteIn, ctx: CurrentAuth, service: _TablesDep
) -> SeatInviteOut:
    """Offer an open chair to someone in my circle (host; lobby, or the seat a Replace opened)."""
    await rate_limit.enforce(rate_limit.SEAT_INVITE_CREATE, f"user:{ctx.user.id}")
    return await service.invite(game_id, body, ctx.user)


@spades_tables_router.get("/{game_id}/qr.svg", response_model=None, response_class=Response)
async def get_spades_table_qr(game_id: _TableId, ctx: CurrentAuth, service: _TablesDep) -> Response:
    """The join link (`/spades?join=<code>`) as a QR code, for the people seated at the table."""
    svg = await service.qr(game_id, ctx.user)
    return Response(
        svg, media_type="image/svg+xml", headers={"Cache-Control": "private, max-age=60"}
    )


@spades_tables_router.get(
    "/{game_id}/events", response_model=None, response_class=StreamingResponse
)
async def spades_table_events(
    game_id: _TableId,
    ctx: CurrentAuth,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """SSE: `state` (my SpView, id = rev) first and on every change, `seat-presence`
    `{seats: boolean[4]}`, `reaction` `{seat, emoji}`, `ended` `{status, result}`, and `ping`
    `{rev}` every 25 s (which marks my seat seen; `rev` is the table's current rev, `{}` if it
    could not be read): a phone whose own rev is lower missed a `state` and should GET the table."""
    queue = await hub.subscribe_game(SPADES, game_id, viewer=str(ctx.user.id))
    try:
        # Own short session: a stream can last an hour and must not hold a pooled connection.
        async with get_session_factory()() as session:
            first = await SpadesTablesService(session).follow(game_id, ctx.user)
    except BaseException:
        hub.unsubscribe_game(game_id, queue)
        raise
    seen = last_event_id is not None and last_event_id.isdigit()
    send_first = not (seen and int(str(last_event_id)) >= (first.rev or 0))
    return StreamingResponse(
        table_stream(
            game_id,
            queue,
            first,
            send_first=send_first,
            on_ping=partial(stamp_table_seen, game_id, ctx.user.id),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@spades_tables_router.delete("/{game_id}", status_code=204)
async def delete_spades_table(game_id: _TableId, ctx: CurrentAuth, service: _TablesDep) -> None:
    """Delete a finished or cancelled table (host or admin); followers' streams end.

    `422 invalid_action {reason: 'not-finished'}` while it is open."""
    await service.delete(game_id, ctx.user)
