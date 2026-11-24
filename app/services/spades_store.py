"""Reading and writing one online Spades table: the locked row, its hands, seats and views.

Shared by the tables service and the table's seat-invite handler (which seats an invitee), so it
never imports the seat-invites service. Writes never commit; callers do.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, User
from app.db.session import get_session_factory
from app.domain.time import from_ms, now_utc, to_ms
from app.exceptions import ApiError, ErrorCode
from app.live.hub import SPADES, notify
from app.repositories.spades_tables import (
    KIND,
    Occupant,
    PendingInvite,
    PlayerCard,
    spades_tables_repository,
)
from app.schemas.spades_tables import SpViewOut
from app.services.presenters import photo_url
from app.services.seat_invite_kinds import INVITE_TTL
from app.services.spades_state import (
    NO_INVITES,
    Invites,
    State,
    apply_state,
    not_found,
    seat_of_user,
    view_body,
    view_out,
)

Hands = list[list[dict[str, Any]]]


@dataclass(frozen=True)
class Bundle:
    """What a table's followers' frames are built from, loaded once per change."""

    state: State
    hands: Hands | None
    invites: Invites


def who_of(card: PlayerCard) -> dict[str, Any]:
    """SpWho: the account in a seat; the photo and look are as they were when it sat down."""
    return {
        "userId": str(card.user_id),
        "playerId": str(card.player_id),
        "name": card.name,
        "emoji": card.emoji,
        "color": card.color,
        "photoUrl": photo_url(card.player_id, card.photo_rev) if card.has_photo else None,
        "look": card.look,
    }


def _open_seat(state: State, seat: int) -> bool:
    """A seat an invitee could take now: empty, in the lobby or during a Replace."""
    empty = state["seats"][seat]["who"] is None
    return empty and (state["status"] == "lobby" or state["paused"] == "replace")


def _seat_index(text: str) -> int | None:
    return int(text) if text in ("0", "1", "2", "3") else None


def seat_invites(state: State, pending: list[PendingInvite]) -> Invites:
    """Each open seat's newest live pending invite."""
    out: Invites = list(NO_INVITES)
    for invite in sorted(pending, key=lambda p: p.created_at):
        seat = _seat_index(invite.seat)
        if seat is None or not _open_seat(state, seat):
            continue
        out[seat] = {
            **{k: v for k, v in who_of(invite.card).items() if k != "playerId"},
            "id": str(invite.id),
        }
    return out


def _occupants(state: State) -> list[tuple[int, str, str]]:
    return [
        (i, s["who"]["playerId"], s["who"]["userId"])
        for i, s in enumerate(state["seats"])
        if s["who"] is not None
    ]


def _changed_hands(before: Hands | None, after: Hands | None) -> dict[int, list[Any]]:
    if after is None:
        return {}
    return {s: h for s, h in enumerate(after) if before is None or before[s] != h}


async def load_bundle(game_id: uuid.UUID) -> tuple[int, Bundle] | None:
    """The live hub's loader: the state, hands and invites once per change; None once gone."""
    async with get_session_factory()() as session:
        state = await spades_tables_repository.payload(session, game_id)
        if state is None:
            return None
        hands = await spades_tables_repository.hands(session, game_id)
        invites = await SpadesStore(session).invites(game_id, state)
    return int(state["rev"]), Bundle(state, hands, invites)


def frame_for(bundle: Bundle, viewer: str | None) -> str:
    """One follower's `state` frame: the viewer is an account id, its seat read from the state."""
    seat = None if viewer is None else seat_of_user(bundle.state, uuid.UUID(viewer))
    return json.dumps(view_body(bundle.state, bundle.hands, seat, bundle.invites))


def presence_frame(viewers: frozenset[str]) -> str:
    """The accounts following the table on any worker; each stream turns them into seats."""
    return json.dumps({"users": sorted(viewers)})


class SpadesStore:
    """One table's row, hands, seats and views."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def locked(self, game_id: uuid.UUID) -> Game:
        """A live online table, locked; 404 otherwise."""
        row = await spades_tables_repository.lock_row(self.session, game_id)
        if row is None or row.kind != KIND or row.deleted_at is not None:
            raise not_found()
        return row

    async def who(self, user: User) -> dict[str, Any]:
        """The account as a seat; 409 `no_account` when it has no player to sit as."""
        card = await spades_tables_repository.card(self.session, user.id)
        if card is None:
            raise ApiError(409, ErrorCode.NO_ACCOUNT, "Link a player to your account to sit.")
        return who_of(card)

    async def invites(self, game_id: uuid.UUID, state: State) -> Invites:
        """The table's live pending invites by seat."""
        since = now_utc() - INVITE_TTL
        pending = await spades_tables_repository.pending_invites(self.session, game_id, since)
        return seat_invites(state, pending)

    async def hands(self, game_id: uuid.UUID) -> Hands | None:
        """The private hands."""
        return await spades_tables_repository.hands(self.session, game_id)

    async def view(
        self, game_id: uuid.UUID, state: State, hands: Hands | None, user: User
    ) -> SpViewOut:
        """The table as this account may see it."""
        invites = await self.invites(game_id, state)
        return view_out(state, hands, seat_of_user(state, user.id), invites)

    async def save(
        self,
        row: Game,
        state: State,
        *,
        hands: tuple[Hands | None, Hands | None] = (None, None),
        data: dict[str, Any] | None = None,
    ) -> None:
        """Store a new state: row, changed hands, seats, hand ends; then NOTIFY on commit."""
        before: State = row.payload
        apply_state(row, state)
        await spades_tables_repository.save(self.session, row)
        await spades_tables_repository.save_hands(
            self.session, row.id, _changed_hands(hands[0], hands[1])
        )
        now = now_utc()
        if _occupants(before) != _occupants(state):
            seated = [
                Occupant(seat=i, player_id=uuid.UUID(p), user_id=uuid.UUID(u))
                for i, p, u in _occupants(state)
            ]
            await spades_tables_repository.sync_seats(self.session, row.id, seated, now)
        if len(state["history"]) > len(before["history"]):
            done = from_ms(int(state["history"][-1]["doneAt"]))
            await spades_tables_repository.mark_done(self.session, row.id, done)
        await self.tell(row.id, data)

    async def tell(self, game_id: uuid.UUID, data: dict[str, Any] | None = None) -> None:
        """NOTIFY the table's followers and its live-link watchers (delivered on commit)."""
        await notify(self.session, game_id, SPADES, data=data)
        await notify(self.session, game_id, "game")


def now_ms() -> int:
    """The server clock in epoch ms."""
    return to_ms(now_utc())


async def stamp_table_seen(game_id: uuid.UUID, user_id: uuid.UUID) -> int | None:
    """Mark the account's seat at this table seen now and return the table's rev (stream pings).

    Own short session; the rev lets a phone that missed a `state` see it is behind.
    """
    async with get_session_factory()() as session:
        await spades_tables_repository.touch_seen(session, game_id, user_id, now_utc())
        rev = await spades_tables_repository.rev(session, game_id)
        await session.commit()
    return rev
