"""Seat invites: a host offers a seat of a server-run game to someone in their circle.

Expiry is worked out on read (30 minutes, or the kind's `stale`), so no job is needed. Every
lifecycle change NOTIFYs the invitee (`seat-invite`, `seat-invite-gone`) and the host
(`seat-invite-update`) on their user streams. A kind joins by adding its handler to
`SEAT_INVITE_KINDS` (see `services/seat_invite_kinds.py`) and calling `create` from its route.
"""

import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import SeatInvite, User
from app.db.session import get_session_factory
from app.domain.time import now_utc, to_ms, to_ms_or_none
from app.exceptions import ApiError, ErrorCode
from app.live.hub import notify_user
from app.logger import get_logger
from app.repositories.circle import CircleRow, circle_repository
from app.repositories.seat_invites import (
    PENDING,
    InviteGame,
    InviteHost,
    InviteView,
    seat_invites_repository,
)
from app.repositories.users import users_repository
from app.schemas.seat_invites import (
    CircleMemberOut,
    CircleOut,
    SeatInviteGoneOut,
    SeatInviteOut,
    SeatInvitesOut,
    SeatInviteUpdateOut,
)
from app.services.ishaara_seat_invites import IshaaraSeatInvites
from app.services.presenters import photo_url
from app.services.seat_invite_kinds import SeatInviteKind, Staleness

logger = get_logger(__name__)

INVITE_TTL = timedelta(minutes=30)
ACCEPTED = "accepted"
DECLINED = "declined"
CANCELLED = "cancelled"
EXPIRED = "expired"

# One handler per kind that takes seat invites; the key is `games.kind` and `seat_invites.kind`.
SEAT_INVITE_KINDS: dict[str, SeatInviteKind] = {"ishaara": IshaaraSeatInvites()}


def _not_found() -> ApiError:
    return ApiError(404, ErrorCode.NOT_FOUND, "Invite not found.")


def _expired() -> ApiError:
    return ApiError(410, ErrorCode.EXPIRED, "This invite has expired.")


def _filled() -> ApiError:
    return ApiError(409, ErrorCode.FILLED, "This table has filled up.")


def _member_out(row: CircleRow) -> CircleMemberOut:
    return CircleMemberOut(
        user_id=row.user_id,
        player_id=row.player_id,
        name=row.name,
        emoji=row.emoji,
        color=row.color,
        photo_url=photo_url(row.player_id, row.photo_rev) if row.has_photo else None,
        last_seen_at=to_ms_or_none(row.last_seen_at),
        last_played_at=to_ms_or_none(row.last_played_at),
    )


def invite_out(invite: SeatInvite, game: InviteGame, host: InviteHost) -> SeatInviteOut:
    """The contract's SeatInviteOut."""
    table = SEAT_INVITE_KINDS[invite.kind].table(game, invite, host.name)
    photo = None
    if host.has_photo and host.player_id is not None:
        photo = photo_url(host.player_id, host.photo_rev)
    return SeatInviteOut.model_validate(
        {
            "id": invite.id,
            "kind": invite.kind,
            "gameId": invite.game_id,
            "seat": invite.seat,
            "status": invite.status,
            "createdAt": to_ms(invite.created_at),
            "from": {
                "userId": host.user_id,
                "name": host.name,
                "emoji": host.emoji,
                "color": host.color,
                "photoUrl": photo,
            },
            "table": {"name": table.name, "seatsTaken": list(table.seats_taken)},
            "partner": None if table.partner is None else {"name": table.partner},
        }
    )


def staleness(view: InviteView, now: datetime) -> Staleness | None:
    """Why a pending invite is out of date: 30 minutes passed, or its kind says so."""
    if now - view.invite.created_at > INVITE_TTL:
        return Staleness.EXPIRED
    return SEAT_INVITE_KINDS[view.invite.kind].stale(view.game, view.invite)


async def stamp_seen(user_id: uuid.UUID) -> int:
    """Mark a user seen now in its own short session (stream open and pings); now in epoch ms."""
    now = now_utc()
    async with get_session_factory()() as session:
        await users_repository.touch_last_seen(session, user_id, now)
        await session.commit()
    return to_ms(now)


async def stream_hello(user_id: uuid.UUID) -> str:
    """The user stream's `hello` frame data, after marking the user seen."""
    return json.dumps({"serverNow": await stamp_seen(user_id)})


class SeatInvitesService:
    """The circle, my invites, and each invite's lifecycle."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def circle(self, user: User) -> CircleOut:
        """`GET /api/me/circle`: invitable members of my circle, last played first."""
        rows = await circle_repository.members(self.session, user.id, user.player_id)
        return CircleOut(items=[_member_out(r) for r in rows])

    async def mine(self, user: User) -> SeatInvitesOut:
        """`GET /api/seat-invites`: my pending invites, out-of-date ones left out."""
        now = now_utc()
        views = await seat_invites_repository.pending_for(self.session, user.id, now - INVITE_TTL)
        items = [invite_out(v.invite, v.game, v.host) for v in views if staleness(v, now) is None]
        return SeatInvitesOut(items=items, server_now=to_ms(now))

    async def create(
        self, *, kind: str, game_id: uuid.UUID, host: User, to_user_id: uuid.UUID, seat: str
    ) -> SeatInviteOut:
        """A host invites someone in their circle to a seat. Expires their earlier pending
        invites to the game, and the seat's, in the same transaction. Commits."""
        handler = SEAT_INVITE_KINDS[kind]
        game = await seat_invites_repository.game(self.session, game_id, lock=True)
        if game is None or game.deleted_at is not None or game.kind != kind:
            raise ApiError(404, ErrorCode.NOT_FOUND, "Game not found.")
        if game.owner_id != host.id:
            raise ApiError(403, ErrorCode.NOT_HOST, "Only the host can invite.")
        to_user = await self._invitee(host, to_user_id)
        await handler.can_invite(self.session, game, to_user, seat)
        now = now_utc()
        for old in await seat_invites_repository.settle_pending(
            self.session, game_id, status=EXPIRED, at=now, to_user=to_user.id, seat=seat
        ):
            await self._tell_settled(old)
        out = await self._insert(kind, game, host, [(seat, to_user.id)], now)
        await self.session.commit()
        logger.info("seat invite sent", extra={"game_id": str(game_id)})
        return out[0]

    async def _invitee(self, host: User, to_user_id: uuid.UUID) -> User:
        to_user = await users_repository.get(self.session, to_user_id)
        if to_user is None or not to_user.active or to_user.player_id is None:
            raise ApiError(409, ErrorCode.NO_ACCOUNT, "They don't have an account yet.")
        in_circle = to_user.id != host.id and await circle_repository.contains(
            self.session, host.id, host.player_id, to_user.player_id
        )
        if not in_circle:
            raise ApiError(409, ErrorCode.NOT_IN_CIRCLE, "Only people you play with.")
        return to_user

    async def invite_seats(
        self, *, kind: str, game_id: uuid.UUID, host: User, seats: list[tuple[str, uuid.UUID]]
    ) -> None:
        """Invites a game makes for itself at create (Ishaara's clue-givers): no checks, no
        commit. The host is never invited."""
        seats = [(seat, user_id) for seat, user_id in seats if user_id != host.id]
        if not seats:
            return
        game = await seat_invites_repository.game(self.session, game_id)
        assert game is not None
        await self._insert(kind, game, host, seats, now_utc())

    async def _insert(
        self,
        kind: str,
        game: InviteGame,
        host: User,
        seats: list[tuple[str, uuid.UUID]],
        now: datetime,
    ) -> list[SeatInviteOut]:
        rows = [
            {
                "id": uuid.uuid4(),
                "kind": kind,
                "game_id": game.id,
                "seat": seat,
                "from_user": host.id,
                "to_user": user_id,
                "status": PENDING,
                "created_at": now,
            }
            for seat, user_id in seats
        ]
        await seat_invites_repository.insert(self.session, rows)
        invites = [SeatInvite(**row) for row in rows]
        host_view = await seat_invites_repository.host(self.session, host.id)
        outs = [invite_out(i, game, host_view) for i in invites]
        for invite, out in zip(invites, outs, strict=True):
            await notify_user(
                self.session, invite.to_user, "seat-invite", out.model_dump(mode="json")
            )
            await self._tell_host(invite)
            await SEAT_INVITE_KINDS[kind].after_change(self.session, invite)
        return outs

    async def accept(self, invite_id: uuid.UUID, user: User) -> SeatInviteOut:
        """`POST /api/seat-invites/{id}/accept`: the invitee takes the seat. Commits.

        410 `expired` once it is out of date or no longer pending; 409 `filled` once the seat or
        game has moved on. Accepting an accepted invite returns it.
        """
        view = await self._invitee_view(invite_id, user)
        invite = view.invite
        if invite.status == ACCEPTED:
            return invite_out(invite, view.game, view.host)
        if invite.status != PENDING:
            raise _expired()
        now = now_utc()
        reason = staleness(view, now)
        if reason is not None:
            await self._settle(invite, EXPIRED, now)
            await self.session.commit()
            raise _filled() if reason == Staleness.FILLED else _expired()
        await SEAT_INVITE_KINDS[invite.kind].on_accept(self.session, invite, user)
        await self._settle(invite, ACCEPTED, now)
        await self.session.commit()
        logger.info("seat invite accepted", extra={"game_id": str(invite.game_id)})
        return invite_out(invite, view.game, view.host)

    async def decline(self, invite_id: uuid.UUID, user: User) -> None:
        """`POST /api/seat-invites/{id}/decline`: the invitee says no. Commits.

        An out-of-date invite is recorded as expired; a settled one is left as it is.
        """
        view = await self._invitee_view(invite_id, user)
        if view.invite.status != PENDING:
            return
        now = now_utc()
        status = DECLINED if staleness(view, now) is None else EXPIRED
        await self._settle(view.invite, status, now)
        await self.session.commit()

    async def cancel(self, invite_id: uuid.UUID, user: User) -> None:
        """`DELETE /api/seat-invites/{id}`: the host takes an invite back. Commits.

        403 `not_host` for the invitee, 404 for anyone else; a settled invite is left as it is.
        """
        view = await seat_invites_repository.view(self.session, invite_id, lock=True)
        if view is None or user.id not in (view.invite.from_user, view.invite.to_user):
            raise _not_found()
        if view.invite.from_user != user.id:
            raise ApiError(403, ErrorCode.NOT_HOST, "Only the host can cancel an invite.")
        if view.invite.status != PENDING:
            return
        await self._settle(view.invite, CANCELLED, now_utc())
        await self.session.commit()

    async def accept_joined(self, game_id: uuid.UUID, user_id: uuid.UUID) -> None:
        """The invitee joined by the game's own route: their pending invite is accepted (no commit)."""
        for invite in await seat_invites_repository.settle_pending(
            self.session, game_id, status=ACCEPTED, at=now_utc(), to_user=user_id
        ):
            await self._tell_settled(invite)

    async def expire_for_game(self, game_id: uuid.UUID) -> None:
        """A game ended or was deleted: its pending invites expire (no commit)."""
        for invite in await seat_invites_repository.settle_pending(
            self.session, game_id, status=EXPIRED, at=now_utc()
        ):
            await self._tell_settled(invite)

    async def _invitee_view(self, invite_id: uuid.UUID, user: User) -> InviteView:
        view = await seat_invites_repository.view(self.session, invite_id, lock=True)
        if view is None or view.invite.to_user != user.id:
            raise _not_found()
        return view

    async def _settle(self, invite: SeatInvite, status: str, at: datetime) -> None:
        await seat_invites_repository.set_status(self.session, invite, status, at)
        await self._tell_settled(invite)

    async def _tell_settled(self, invite: SeatInvite) -> None:
        gone = SeatInviteGoneOut.model_validate({"id": invite.id, "status": invite.status})
        await notify_user(
            self.session, invite.to_user, "seat-invite-gone", gone.model_dump(mode="json")
        )
        await self._tell_host(invite)
        await SEAT_INVITE_KINDS[invite.kind].after_change(self.session, invite)

    async def _tell_host(self, invite: SeatInvite) -> None:
        update = SeatInviteUpdateOut.model_validate(
            {
                "id": invite.id,
                "status": invite.status,
                "gameId": invite.game_id,
                "seat": invite.seat,
            }
        )
        await notify_user(
            self.session, invite.from_user, "seat-invite-update", update.model_dump(mode="json")
        )
