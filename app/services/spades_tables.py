"""Spades online tables, run by the server: create, join, rules, view, actions, lists and delete.

`games.payload` is the public state; the hands live in `spades_hands` and leave the server only
as `viewFor`'s `me.hand`, to their own seat. Every write holds the row FOR UPDATE, so actions on
one table apply one at a time. The draw for deal and the deal use `secrets.SystemRandom`.
"""

import copy
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, User
from app.domain.ishaara.text import js_trim, utf16_len
from app.domain.spades import NAME_MAX, apply_action, deck, new_table, rematch_table, rules_ok
from app.domain.spades import set_rules as domain_set_rules
from app.domain.spades import sit as domain_sit
from app.domain.spades.stats import player_rows
from app.domain.spades.types import ApplyCtx
from app.domain.time import from_ms, now_utc, to_ms
from app.exceptions import ApiError, ErrorCode
from app.live.hub import STATELESS, LiveMessage
from app.logger import get_logger
from app.render import run_in_thread
from app.render.qr import qr_svg
from app.repositories.app_state import app_state_repository
from app.repositories.derived import derived_repository
from app.repositories.games import games_repository
from app.repositories.share_links import share_links_repository
from app.repositories.spades_derived import spades_derived_repository
from app.repositories.spades_tables import KIND, Occupant, spades_tables_repository
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
from app.security.rate_limit import SPADES_REACTION
from app.services.pagination import decode_cursor, encode_cursor
from app.services.progression.hooks import progress_game
from app.services.seat_invites import SeatInvitesService
from app.services.spades_derive import game_player_dicts
from app.services.spades_state import (
    State,
    apply_state,
    not_found,
    refusal_error,
    seat_of_user,
    stale_error,
    table_summary_out,
)
from app.services.spades_store import Hands, SpadesStore, now_ms
from app.settings import settings

logger = get_logger(__name__)

# Open tables untouched this long are ended (auto-end job).
IDLE_MS = 2 * 60 * 60 * 1000
# A join code: 4 characters without lookalikes (no I, O, 0 or 1).
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 4
CODE_TRIES = 20
# Settled by the domain's own rev check (timeout) or never stored (react).
_NO_STALE_CHECK = frozenset({"timeout", "react"})
_RANDOM = secrets.SystemRandom()


def new_code() -> str:
    """A random join code."""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def _draw() -> list[dict[str, Any]]:
    """Four distinct cards for the draw for deal."""
    return _RANDOM.sample(deck(), 4)


def _deal() -> list[list[dict[str, Any]]]:
    """The whole deck shuffled, 13 to each seat."""
    cards = deck()
    _RANDOM.shuffle(cards)
    return [cards[s * 13 : s * 13 + 13] for s in range(4)]


def _clean_name(raw: str) -> str:
    """A table name as the domain accepts it; 422 `invalid` otherwise."""
    name = js_trim(raw)
    if not name or utf16_len(name) > NAME_MAX:
        raise ApiError(422, ErrorCode.INVALID, "Bad name.", fields={"name": "1 to 40 characters"})
    return name


def _uuid(raw: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except ValueError as exc:
        raise ApiError(422, ErrorCode.INVALID, "Bad id.", fields={"id": "not a uuid"}) from exc


def _cursor_id(key: str) -> uuid.UUID:
    try:
        return uuid.UUID(key)
    except ValueError as exc:
        raise ApiError(
            422, ErrorCode.INVALID, "Bad cursor.", fields={"cursor": "not a valid cursor"}
        ) from exc


def _seat_param(raw: Any) -> Any:
    """The join body's seat as the domain reads it (a whole float counts as a seat)."""
    return int(raw) if isinstance(raw, float) and raw.is_integer() else raw


def _ending(before: State, after: State) -> bool:
    return before["status"] in ("lobby", "playing") and after["status"] in ("finished", "cancelled")


def _hand_ended(before: State, after: State) -> bool:
    return len(after["history"]) > len(before["history"])


def _ended_event(state: State) -> dict[str, Any]:
    return {"ended": {"status": state["status"], "result": state["result"]}}


@dataclass(frozen=True)
class _Applied:
    state: State
    hands: Hands | None


class SpadesTablesService:
    """Online Spades tables."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.store = SpadesStore(session)

    async def create(self, body: CreateTableIn, user: User) -> tuple[SpViewOut, bool]:
        """`POST /tables`: (view, created). The same host re-sending the same id gets it back."""
        rules = body.rules.model_dump(by_alias=True)
        if not rules_ok(rules):
            raise ApiError(422, ErrorCode.INVALID, "Bad rules.", fields={"rules": "bad-rules"})
        await games_repository.lock(self.session, body.id)
        existing = await games_repository.get(self.session, body.id, kind=None)
        if existing is not None:
            if existing.kind != KIND or existing.owner_id != user.id or existing.deleted_at:
                raise ApiError(409, ErrorCode.ID_CONFLICT, "This id is already used.")
            hands = await self.store.hands(existing.id)
            return await self.store.view(existing.id, existing.payload, hands, user), False
        who = await self.store.who(user)
        name = _clean_name(body.name if body.name is not None else f"{who['name']}'s table")
        make = {"id": str(body.id), "name": name, "rules": rules, "host": who, "now": now_ms()}
        state = await self._insert_table(body.id, user.id, make)
        await app_state_repository.bump(self.session)
        await self.session.commit()
        logger.info("spades table created", extra={"game_id": str(body.id)})
        return await self.store.view(body.id, state, None, user), True

    async def _insert_table(
        self, game_id: uuid.UUID, owner_id: uuid.UUID, make: dict[str, Any]
    ) -> State:
        """A new open table under a fresh code (retried when another table took it meanwhile)."""
        for _ in range(CODE_TRIES):
            code = new_code()
            if await spades_tables_repository.code_open(self.session, code) is not None:
                continue
            state: State = new_table(code=code, **make)
            row = Game(id=game_id, kind=KIND, owner_id=owner_id)
            apply_state(row, state)
            try:
                async with self.session.begin_nested():
                    await spades_tables_repository.insert_game(self.session, row)
            except IntegrityError:
                self.session.expunge(row)
                continue
            await self._seat_rows(game_id, state)
            await derived_repository.refresh_player_counters(self.session, _player_ids(state))
            return state
        raise ApiError(409, ErrorCode.ID_CONFLICT, "No free table code; try again.")

    async def _seat_rows(self, game_id: uuid.UUID, state: State) -> None:
        seated = [
            Occupant(i, uuid.UUID(s["who"]["playerId"]), uuid.UUID(s["who"]["userId"]))
            for i, s in enumerate(state["seats"])
            if s["who"] is not None
        ]
        await spades_tables_repository.sync_seats(self.session, game_id, seated, now_utc())

    async def by_code(self, code: str) -> TableRefOut:
        """`GET /tables/by-code/{code}`: the open table with this code."""
        found = await spades_tables_repository.code_open(self.session, code.strip().upper())
        if found is None:
            raise not_found()
        return TableRefOut(id=found)

    async def get(self, game_id: uuid.UUID, user: User) -> SpViewOut:
        """`GET /tables/{id}`: anyone signed in; only a seated viewer sees cards (their own)."""
        row = await spades_tables_repository.get_row(self.session, game_id)
        if row is None:
            raise not_found()
        hands = await self.store.hands(game_id)
        return await self.store.view(game_id, row.payload, hands, user)

    async def follow(self, game_id: uuid.UUID, user: User) -> LiveMessage:
        """The first `state` frame for an events stream; marks the account's seat seen."""
        view = await self.get(game_id, user)
        if view.me.seat is not None:
            await spades_tables_repository.touch_seen(self.session, game_id, user.id, now_utc())
            await self.session.commit()
        return LiveMessage(kind="state", rev=int(view.table["rev"]), data=view.model_dump_json())

    async def join(self, game_id: uuid.UUID, body: JoinTableIn, user: User) -> SpViewOut:
        """`POST /tables/{id}/join`: take or move to a seat (lobby), or the seat a Replace opened."""
        row = await self.store.locked(game_id)
        who = await self.store.who(user)
        result = domain_sit(row.payload, who, _seat_param(body.seat), now_ms())
        hands = await self.store.hands(game_id)
        if not result.ok:
            assert result.error is not None
            view = await self.store.view(game_id, row.payload, hands, user)
            raise _join_error(result.error, view)
        if result.state is not row.payload:
            await self.store.save(row, result.state)
            await self.session.commit()
        return await self.store.view(game_id, result.state, hands, user)

    async def set_rules(self, game_id: uuid.UUID, body: TableRulesIn, user: User) -> SpViewOut:
        """`PATCH /tables/{id}/rules`: the host, in the lobby."""
        row = await self.store.locked(game_id)
        actor = seat_of_user(row.payload, user.id)
        rules = body.rules.model_dump(by_alias=True)
        result = domain_set_rules(row.payload, actor, rules, body.name, now_ms())
        if not result.ok:
            assert result.error is not None
            view = await self.store.view(game_id, row.payload, None, user)
            raise refusal_error(result.error, view)
        await self.store.save(row, result.state)
        await self.session.commit()
        return await self.store.view(game_id, result.state, None, user)

    async def act(self, game_id: uuid.UUID, body: SpActionIn, user: User) -> SpViewOut:
        """`POST /tables/{id}/actions`: replay, stale, then the rules engine."""
        row = await self.store.locked(game_id)
        state: State = row.payload
        hands = await self.store.hands(game_id)
        if await spades_tables_repository.action_seen(self.session, game_id, body.action_id):
            return await self.store.view(game_id, state, hands, user)
        if body.type not in _NO_STALE_CHECK and body.rev != row.rev:
            raise stale_error(await self.store.view(game_id, state, hands, user))
        action = body.action()
        if action["type"] == "rematch":
            await self._check_rematch_id(action)
        seat = seat_of_user(state, user.id)
        ctx = await self._ctx(game_id, state, action, hands, body.rev)
        result = apply_action(state, action, seat, ctx)
        if not result.ok:
            assert result.error is not None
            raise refusal_error(result.error, await self.store.view(game_id, state, hands, user))
        if action["type"] == "react":
            return await self._react(game_id, state, hands, seat, action, user)
        if seat is not None:
            await spades_tables_repository.touch_seen(self.session, game_id, user.id, now_utc())
        if result.noop:
            await self.session.commit()
            return await self.store.view(game_id, state, hands, user)
        applied = _Applied(result.state, result.hands)
        await self._applied(row, applied, hands, body, action, user)
        return await self.store.view(game_id, applied.state, applied.hands, user)

    async def _ctx(
        self,
        game_id: uuid.UUID,
        state: State,
        action: dict[str, Any],
        hands: Hands | None,
        rev: int,
    ) -> ApplyCtx:
        """The server's part of an action: its clock, the hands, any randomness, last seen."""
        kind, turn = action["type"], state.get("turn")
        seen = None
        if kind == "timeout" and turn is not None:
            # Timer off: the turn's seat counts as away 60 s after it was last seen.
            at = await spades_tables_repository.last_seen(self.session, game_id, int(turn))
            seen = None if at is None else to_ms(at)
        return ApplyCtx(
            now=now_ms(),
            hands=hands,
            draw=_draw() if kind == "drawForDeal" else None,
            deal=_deal() if kind == "deal" else None,
            rev=rev,
            last_seen_at=seen,
        )

    async def _check_rematch_id(self, action: dict[str, Any]) -> None:
        game_id = _uuid(action.get("id"))
        if await games_repository.get(self.session, game_id, kind=None) is not None:
            raise ApiError(409, ErrorCode.ID_CONFLICT, "This id is already used.")
        action["id"] = str(game_id)

    async def _react(
        self,
        game_id: uuid.UUID,
        state: State,
        hands: Hands | None,
        seat: int | None,
        action: dict[str, Any],
        user: User,
    ) -> SpViewOut:
        """A reaction is never stored: it goes to the table's followers, 1 per 2 s per seat."""
        if not SPADES_REACTION.hit(f"{game_id}:{seat}"):
            raise ApiError(429, ErrorCode.RATE_LIMITED, "Easy there.", retryAfter=2)
        reaction = {"seat": seat, "emoji": action.get("emoji")}
        await self.store.tell(game_id, {"reaction": reaction, STATELESS: True})
        await self.session.commit()
        return await self.store.view(game_id, state, hands, user)

    async def _applied(
        self,
        row: Game,
        applied: _Applied,
        hands: Hands | None,
        body: SpActionIn,
        action: dict[str, Any],
        user: User,
    ) -> None:
        before: State = row.payload
        state = applied.state
        ending = _ending(before, state)
        data = _ended_event(state) if ending else None
        await self.store.save(row, state, hands=(hands, applied.hands), data=data)
        await spades_tables_repository.add_action(
            self.session,
            game_id=row.id,
            action_id=body.action_id,
            body=action,
            at=now_utc(),
            by_user_id=user.id,
        )
        if action["type"] == "rematch":
            await self._open_rematch(row, state, bool(action.get("swap")))
        if ending:
            await self.finish_derived(row.id, state)
        elif _hand_ended(before, state):
            # Hand moments are earned as each hand ends, not only at the end of the game.
            await progress_game(self.session, KIND, row.id, _player_ids(state), finished=False)
        await self.session.commit()

    async def _open_rematch(self, row: Game, old: State, swap: bool) -> None:
        """The rematch's lobby: same people, rules and name, nobody ready, a new code."""
        game_id = uuid.UUID(old["next"])
        code = new_code()
        for _ in range(CODE_TRIES):
            if await spades_tables_repository.code_open(self.session, code) is None:
                break
            code = new_code()
        state = rematch_table(old, id=str(game_id), code=code, now=now_ms(), swap=swap)
        fresh = Game(id=game_id, kind=KIND, owner_id=row.owner_id)
        apply_state(fresh, state)
        await spades_tables_repository.insert_game(self.session, fresh)
        await self._seat_rows(game_id, state)
        await derived_repository.refresh_player_counters(self.session, _player_ids(state))

    async def finish_derived(self, game_id: uuid.UUID, state: State) -> None:
        """A table just ended: its board rows (none when cancelled), counters, invites."""
        result = state["result"]
        if result is not None:
            seats = [None if s["who"] is None else s["who"]["playerId"] for s in state["seats"]]
            rows = player_rows(state["history"], seats, result)
            finished_at = from_ms(int(state["finishedAt"]))
            dicts = game_player_dicts(game_id, KIND, rows, finished_at, result["endedBy"])
            await spades_derived_repository.replace_players(self.session, game_id, dicts)
        players = _player_ids(state)
        await derived_repository.refresh_player_counters(self.session, players)
        await progress_game(self.session, KIND, game_id, players, finished=result is not None)
        await app_state_repository.bump(self.session)
        await SeatInvitesService(self.session).expire_for_game(game_id)

    async def invite(self, game_id: uuid.UUID, body: SeatInviteIn, user: User) -> SeatInviteOut:
        """`POST /tables/{id}/seat-invites`: the host offers an open chair. Commits."""
        return await SeatInvitesService(self.session).create(
            kind=KIND, game_id=game_id, host=user, to_user_id=body.user_id, seat=body.seat
        )

    async def list_page(
        self, user: User, *, status: str | None, cursor: str | None, limit: int
    ) -> SpTablesPageOut:
        """`GET /tables`: tables I sit (or sat) at, newest change first."""
        if user.player_id is None:
            return SpTablesPageOut(items=[], next_cursor=None)
        before = None
        if cursor is not None:
            at, key = decode_cursor(cursor)
            before = (at, _cursor_id(key))
        rows = await spades_tables_repository.list_page(
            self.session, user.player_id, status, before, limit + 1
        )
        page = rows[:limit]
        items = [
            table_summary_out(
                r.game_id, r.summary, player_id=user.player_id, is_host=r.owner_id == user.id
            )
            for r in page
        ]
        next_cursor = None
        if len(rows) > limit:
            next_cursor = encode_cursor(page[-1].updated_at, str(page[-1].game_id))
        return SpTablesPageOut(items=items, next_cursor=next_cursor)

    async def qr(self, game_id: uuid.UUID, user: User) -> str:
        """The table's join link as a QR code (SVG), for the people seated at it."""
        row = await spades_tables_repository.get_row(self.session, game_id)
        if row is None or seat_of_user(row.payload, user.id) is None:
            raise not_found()
        url = f"{settings.APP_ORIGIN}/spades?join={row.payload['code']}"
        return await run_in_thread(qr_svg, url)

    async def delete(self, game_id: uuid.UUID, user: User, *, force: bool = False) -> None:
        """`DELETE /tables/{id}`: the host or admin, once finished or cancelled. Commits.

        `force` (deleting a user's games) deletes an open table too.
        """
        row = await self.store.locked(game_id)
        if row.owner_id != user.id and user.role != "admin":
            raise ApiError(403, ErrorCode.FORBIDDEN, "Only the host or the admin can delete it.")
        if row.status == "active" and not force:
            view = await self.store.view(game_id, row.payload, None, user)
            raise refusal_error({"code": "invalid_action", "reason": "not-finished"}, view)
        players = set(await spades_tables_repository.delete_seats(self.session, game_id))
        players |= set(await spades_derived_repository.player_ids_in_game(self.session, game_id))
        await spades_derived_repository.delete_players(self.session, game_id)
        await spades_tables_repository.delete_hands(self.session, game_id)
        await games_repository.soft_delete(self.session, game_id)
        await derived_repository.refresh_player_counters(self.session, players)
        await progress_game(self.session, KIND, game_id, players, finished=False, void=True)
        await share_links_repository.revoke(self.session, game_id, ("live", "recap"))
        await self.store.tell(game_id)
        await SeatInvitesService(self.session).expire_for_game(game_id)
        await app_state_repository.bump(self.session)
        await self.session.commit()
        logger.info("spades table deleted", extra={"game_id": str(game_id)})

    async def delete_owned(self, owner: User, actor: User) -> int:
        """Delete every live table `owner` hosts, open ones too, as `actor`, each via `delete`."""
        deleted = 0
        for game_id in await games_repository.owned_ids(self.session, owner.id, kind=KIND):
            try:
                await self.delete(game_id, actor, force=True)
            except ApiError as error:
                # Another request removed it first; the goal is the same.
                if error.status != 404:
                    raise
                continue
            deleted += 1
        return deleted

    async def reseat(self, row: Game, from_id: uuid.UUID, into_id: uuid.UUID) -> None:
        """Move a seat to another registry player (admin merge); keeps the rev.

        The game's own achievements follow the seat; careers are the caller's.
        """
        state = _reseated(row.payload, str(from_id), str(into_id))
        await spades_tables_repository.reseat(self.session, row.id, from_id, into_id)
        await spades_derived_repository.reseat_players(self.session, row.id, from_id, into_id)
        apply_state(row, state)
        await spades_tables_repository.save(self.session, row)
        finished = state["status"] == "finished" and state["result"] is not None
        await progress_game(self.session, KIND, row.id, set(), finished=finished, career=False)
        await self.store.tell(row.id)

    async def end_idle(self, game_id: uuid.UUID) -> bool:
        """Auto-end: an open table idle for 2 hours ends as the host's End game would, or is
        cancelled when no hand was finished. Commits."""
        row = await spades_tables_repository.lock_row(self.session, game_id)
        idle_before = now_utc() - timedelta(milliseconds=IDLE_MS)
        # Re-checked under the lock: an action may have moved it on meanwhile.
        if row is None or not _idle(row, idle_before):
            return False
        state: State = row.payload
        kind = "endGame" if state["status"] == "playing" and state["history"] else "cancelGame"
        hands = await self.store.hands(game_id)
        ctx = ApplyCtx(now=now_ms(), hands=hands)
        result = apply_action(state, {"type": kind}, state["host"], ctx)
        if not result.ok:
            logger.warning("spades idle end refused", extra={"game_id": str(game_id)})
            return False
        await self.store.save(row, result.state, data=_ended_event(result.state))
        await self.finish_derived(game_id, result.state)
        await self.session.commit()
        return True


def _idle(row: Game, idle_before: datetime) -> bool:
    return (
        row.kind == KIND
        and row.deleted_at is None
        and row.status == "active"
        and row.updated_at < idle_before
    )


def _player_ids(state: State) -> set[uuid.UUID]:
    """Registry ids of everyone seated, and of everyone who played a finished hand."""
    ids = {s["who"]["playerId"] for s in state["seats"] if s["who"] is not None}
    ids |= {p for h in state["history"] for p in h.get("players") or [] if p}
    return {uuid.UUID(p) for p in ids}


def _reseated(state: State, from_id: str, into_id: str) -> State:
    out = copy.deepcopy(state)
    for seat in out["seats"]:
        if seat["who"] is not None and seat["who"].get("playerId") == from_id:
            seat["who"]["playerId"] = into_id
    for hand in out["history"]:
        players = hand.get("players")
        if isinstance(players, list):
            hand["players"] = [into_id if p == from_id else p for p in players]
    return out


def _join_error(error: dict[str, str], view: SpViewOut) -> ApiError:
    """`full` and `seat-taken` are 409s of their own; the rest are the domain's refusals."""
    game = view.model_dump(mode="json")
    reason = error.get("reason")
    if reason == "full":
        return ApiError(409, ErrorCode.FULL, "The table is full.", game=game)
    if reason == "seat-taken":
        return ApiError(409, ErrorCode.SEAT_TAKEN, "That seat is taken.", game=game)
    return refusal_error(error, view)
