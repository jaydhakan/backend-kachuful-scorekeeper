"""Ishaara games, run by the server: create, read, the key, actions, lists and delete.

The key lives only in `ishaara_keys`; `games.payload` is the public state the domain writes. Every
write holds the game row FOR UPDATE, so actions on one game apply one at a time.
"""

import copy
import json
import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game, IshaaraRole, User
from app.db.session import get_session_factory
from app.domain.enums import GameKind
from app.domain.ishaara.deal import deal_key
from app.domain.ishaara.rules import apply_action, join, new_game
from app.domain.ishaara.stats import player_rows
from app.domain.ishaara.types import TABLE, Actor, State, as_team
from app.domain.ishaara.validate import validate_new
from app.domain.time import from_ms, now_utc, to_ms
from app.exceptions import ApiError, ErrorCode
from app.live.hub import ISHAARA, LiveMessage, notify
from app.logger import get_logger
from app.repositories.app_state import app_state_repository
from app.repositories.derived import derived_repository
from app.repositories.games import games_repository
from app.repositories.ishaara import CLUE_GIVER, IshaaraListRow, ListScope, ishaara_repository
from app.repositories.ishaara_derived import ishaara_derived_repository
from app.schemas.ishaara import (
    ActionIn,
    CreateIshaaraIn,
    IshaaraGameOut,
    IshaaraKeyOut,
    IshaaraPageOut,
    IshaaraSummaryOut,
)
from app.services.games import resolve_players
from app.services.ishaara_state import (
    Access,
    abandoned,
    access_of,
    action_error,
    apply_state,
    game_out,
    id_conflict,
    invalid_game,
    not_found,
    not_your_turn,
    seat_ids,
)
from app.services.pagination import decode_cursor, encode_cursor
from app.settings import settings

logger = get_logger(__name__)

KIND = GameKind.ISHAARA
# Unfinished games untouched this long are ended as abandoned (auto-end job).
IDLE_MS = 2 * 60 * 60 * 1000
TABLE_ACTIONS = frozenset({"guess", "endTurn", "timeout", "undoLast", "resign", "skip"})


def actor_for(action_type: str, access: Access) -> Actor | None:
    """Who the account acts as for this type: the host's table, else its clue-giver seat."""
    if action_type in TABLE_ACTIONS:
        return TABLE if access.host else None
    if access.team is None:
        return None
    return Actor(kind="clue-giver", team=as_team(access.team))


def role_rows(
    seats: list[dict[str, Any]], accounts: dict[uuid.UUID, uuid.UUID]
) -> list[dict[str, Any]]:
    """`ishaara_roles` rows for every seat, with the account linked to it now."""
    return [
        {
            "player_id": uuid.UUID(s["playerId"]),
            "user_id": accounts.get(uuid.UUID(s["playerId"])),
            "team": s["team"],
            "role": s["role"],
        }
        for s in seats
    ]


def check_clue_accounts(seats: list[dict[str, Any]], accounts: dict[uuid.UUID, uuid.UUID]) -> None:
    """Both clue-giver seats are linked to accounts, and not to the same one."""
    givers = [accounts.get(uuid.UUID(s["playerId"])) for s in seats if s["role"] == CLUE_GIVER]
    if any(g is None for g in givers) or len(set(givers)) != len(givers):
        raise invalid_game("clue-giver-account")


def _summary_out(row: IshaaraListRow, team: str | None) -> IshaaraSummaryOut:
    return IshaaraSummaryOut.model_validate(
        {**row.summary, "host": {"name": row.host_name}, "myTeam": team}
    )


def _cursor_id(key: str) -> uuid.UUID:
    try:
        return uuid.UUID(key)
    except ValueError as exc:
        raise ApiError(
            422, ErrorCode.INVALID, "Bad cursor.", fields={"cursor": "not a valid cursor"}
        ) from exc


async def load_game_event(game_id: uuid.UUID) -> tuple[int, str] | None:
    """The live hub's loader: (rev, `state` frame data), or None once the game is gone."""
    async with get_session_factory()() as session:
        state = await ishaara_repository.payload(session, game_id)
    if state is None:
        return None
    return int(state["rev"]), json.dumps({"game": state, "serverNow": to_ms(now_utc())})


class IshaaraGamesService:
    """Create, read, key, act, list and delete Ishaara games."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def locked(self, game_id: uuid.UUID) -> Game:
        """A live Ishaara game row, locked; 404 otherwise."""
        row = await ishaara_repository.lock_row(self.session, game_id)
        if row is None or row.kind != KIND or row.deleted_at is not None:
            raise not_found()
        return row

    async def seen_by(
        self, row: Game, user: User, *, admin_sees: bool = True
    ) -> tuple[list[IshaaraRole], Access]:
        """Roles and the account's access; 404 for anyone who may not see the game."""
        roles = await ishaara_repository.roles(self.session, row.id)
        access = access_of(row, roles, user)
        if not access.member and not (admin_sees and user.role == "admin"):
            raise not_found()
        return roles, access

    async def save_state(self, row: Game, state: State, data: dict[str, Any] | None = None) -> None:
        """Store a new state and tell the game's followers once the transaction commits."""
        apply_state(row, state)
        await ishaara_repository.save(self.session, row)
        await notify(self.session, row.id, ISHAARA, data=data)

    async def _registry_seats(self, seats: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Seats with merged players rewritten; 409 `unknown_player` when one isn't registered."""
        try:
            resolved, _ = await resolve_players(self.session, {"players": seats})
        except ApiError as error:
            if error.code != ErrorCode.UNKNOWN_PLAYER:
                raise
            raise ApiError(409, ErrorCode.UNKNOWN_PLAYER, error.message, **error.extra) from error
        out: list[dict[str, Any]] = resolved["players"]
        return out

    async def start(
        self,
        *,
        game_id: uuid.UUID,
        seats: list[dict[str, Any]],
        words: list[str],
        rules: dict[str, Any],
        owner: User,
        starting: str | None = None,
        match: dict[str, Any] | None = None,
    ) -> State:
        """Validate, deal the key with `secrets` and store a new waiting game (no commit)."""
        problem = validate_new(seats, words, rules)
        if problem is not None:
            raise invalid_game(problem)
        accounts = await ishaara_repository.accounts_of(
            self.session, [uuid.UUID(s["playerId"]) for s in seats]
        )
        check_clue_accounts(seats, accounts)
        key, first = deal_key(starting)
        state = new_game(
            game_id=str(game_id),
            players=seats,
            words=words,
            rules=rules,
            starting=first,
            now=to_ms(now_utc()),
            match=match,
        )
        row = Game(id=game_id, kind=KIND, owner_id=owner.id)
        apply_state(row, state)
        await ishaara_repository.insert_game(self.session, row, key, role_rows(seats, accounts))
        await derived_repository.refresh_player_counters(self.session, seat_ids(state))
        await app_state_repository.bump(self.session)
        return state

    async def create(self, body: CreateIshaaraIn, user: User) -> tuple[IshaaraGameOut, bool]:
        """`POST /games`: (game, created). The same host re-sending the same id gets it back."""
        seats = [s.model_dump(exclude_none=True) for s in body.players]
        rules = body.rules.model_dump()
        problem = validate_new(seats, body.words, rules)
        if problem is not None:
            raise invalid_game(problem)
        await games_repository.lock(self.session, body.id)
        existing = await games_repository.get(self.session, body.id, kind=None)
        if existing is not None:
            if existing.kind != KIND or existing.owner_id != user.id or existing.deleted_at:
                raise id_conflict()
            _, access = await self.seen_by(existing, user)
            return game_out(existing.payload, access), False
        seats = await self._registry_seats(seats)
        state = await self.start(
            game_id=body.id, seats=seats, words=body.words, rules=rules, owner=user
        )
        await self.session.commit()
        logger.info("ishaara game created", extra={"game_id": str(body.id)})
        team = next(
            (
                s["team"]
                for s in seats
                if s["role"] == CLUE_GIVER and s["playerId"] == str(user.player_id)
            ),
            None,
        )
        return game_out(state, Access(host=True, team=team, member=True)), True

    async def get(self, game_id: uuid.UUID, user: User) -> IshaaraGameOut:
        """`GET /games/{id}`: members and the admin."""
        row = await ishaara_repository.get_row(self.session, game_id)
        if row is None or row.deleted_at is not None:
            raise not_found()
        _, access = await self.seen_by(row, user)
        return game_out(row.payload, access)

    async def follow(self, game_id: uuid.UUID, user: User) -> LiveMessage:
        """The first `state` frame for an events stream, after the same check as `get`."""
        out = await self.get(game_id, user)
        data = json.dumps({"game": out.game, "serverNow": out.server_now})
        return LiveMessage(kind="state", rev=int(out.game["rev"]), data=data)

    async def key(self, game_id: uuid.UUID, user: User) -> IshaaraKeyOut:
        """`GET /games/{id}/key`: the two clue-giver accounts only. The first open joins the team."""
        row = await self.locked(game_id)
        _, access = await self.seen_by(row, user)
        if access.team is None:
            raise ApiError(403, ErrorCode.FORBIDDEN, "Only the clue-givers see the key.")
        key = await ishaara_repository.key(self.session, game_id)
        joined = join(row.payload, access.team, to_ms(now_utc()))
        if joined is not row.payload:
            await self.save_state(row, joined, {"joined": {"team": access.team}})
            await self.session.commit()
            logger.info("ishaara clue-giver joined", extra={"game_id": str(game_id)})
        return IshaaraKeyOut.model_validate({"key": key, "team": access.team})

    async def act(self, game_id: uuid.UUID, body: ActionIn, user: User) -> IshaaraGameOut:
        """`POST /games/{id}/actions`: replay, stale, role, then the rules engine."""
        row = await self.locked(game_id)
        _, access = await self.seen_by(row, user, admin_sees=False)
        if await ishaara_repository.action_seen(self.session, game_id, body.action_id):
            return game_out(row.payload, access)
        if body.rev != row.rev:
            game = game_out(row.payload, access).model_dump(mode="json")
            raise ApiError(409, ErrorCode.STALE, "The game has moved on.", game=game)
        actor = actor_for(body.type, access)
        if actor is None:
            raise not_your_turn(game_out(row.payload, access).model_dump(mode="json"))
        key = await ishaara_repository.key(self.session, game_id)
        now = now_utc()
        result = apply_action(row.payload, key, body.action(), actor, to_ms(now))
        if not result.ok:
            await self._keep_settled(row, result.state)
            assert result.error is not None
            raise action_error(result.error, game_out(result.state, access))
        await self._applied(row, result.state, body, user, now)
        return game_out(result.state, access)

    async def _keep_settled(self, row: Game, state: State) -> None:
        """A rejected action still stores an overdue timer's end of turn."""
        if state["rev"] != row.rev:
            await self.save_state(row, state)
            await self.session.commit()

    async def _applied(self, row: Game, state: State, body: ActionIn, user: User, now: Any) -> None:
        finishing = state["status"] == "finished" and row.status != "finished"
        ended = {"ended": {"winner": state["winner"], "endedBy": state["endedBy"]}}
        await self.save_state(row, state, ended if finishing else None)
        await ishaara_repository.add_action(
            self.session,
            game_id=row.id,
            action_id=body.action_id,
            body=body.action(),
            at=now,
            by_user_id=user.id,
        )
        if finishing:
            await self.finish_derived(row.id, state)
        await self.session.commit()

    async def finish_derived(self, game_id: uuid.UUID, state: State) -> None:
        """A game just ended: its player rows and day rollups (none when abandoned), counters."""
        players = seat_ids(state)
        if state["endedBy"] != "abandoned":
            finished_at = from_ms(state["finishedAt"])
            rows = [
                {
                    "game_id": game_id,
                    "player_id": uuid.UUID(r["playerId"]),
                    "team": r["team"],
                    "role": r["role"],
                    "won": r["won"],
                    "words_found": r["wordsFound"],
                    "wrong_picks": r["wrongPicks"],
                    "trap_hit": r["trapHit"],
                    "clues_given": r["cluesGiven"],
                    "avg_clue_number": r["avgClueNumber"],
                    "penalties": r["penalties"],
                    "finished_at": finished_at,
                }
                for r in player_rows(state)
            ]
            await ishaara_derived_repository.insert_rows(self.session, rows)
            day = finished_at.astimezone(settings.tz).date()
            await ishaara_derived_repository.rebuild_player_days(
                self.session, {(p, day) for p in players}, settings.APP_TZ
            )
        await derived_repository.refresh_player_counters(self.session, players)
        await app_state_repository.bump(self.session)

    async def list_page(
        self,
        user: User,
        *,
        clue_giver: bool,
        status: str | None,
        involving: uuid.UUID | None,
        cursor: str | None,
        limit: int,
    ) -> IshaaraPageOut:
        """`GET /games`: games the account hosts or sits in (admin: any player's with `involving`)."""
        before = None
        if cursor is not None:
            at, last = decode_cursor(cursor)
            before = (at, _cursor_id(last))
        scope = ListScope(
            user_id=user.id,
            player_id=user.player_id,
            everyone=user.role == "admin" and involving is not None,
            clue_giver=clue_giver,
            status=status,
            involving=involving,
        )
        rows = await ishaara_repository.list_page(self.session, scope, before, limit + 1)
        page = rows[:limit]
        teams = await ishaara_repository.clue_teams(
            self.session, user.id, [r.game_id for r in page]
        )
        next_cursor = None
        if len(rows) > limit:
            last_row = page[-1]
            next_cursor = encode_cursor(last_row.updated_at, str(last_row.game_id))
        return IshaaraPageOut(
            items=[_summary_out(r, teams.get(r.game_id)) for r in page], next_cursor=next_cursor
        )

    async def delete(self, game_id: uuid.UUID, user: User) -> None:
        """`DELETE /games/{id}` (host or admin): soft delete; seats and board rows go. Commits."""
        row = await self.locked(game_id)
        roles, access = await self.seen_by(row, user)
        if not access.host and user.role != "admin":
            raise ApiError(403, ErrorCode.FORBIDDEN, "Only the host or the admin can delete it.")
        moments = await ishaara_derived_repository.moments(self.session, game_id)
        players = {r.player_id for r in roles} | {p for p, _ in moments}
        await ishaara_derived_repository.delete_rows(self.session, game_id)
        await ishaara_repository.delete_roles(self.session, game_id)
        await games_repository.soft_delete(self.session, game_id)
        tz = settings.tz
        pairs = {(p, at.astimezone(tz).date()) for p, at in moments}
        await ishaara_derived_repository.rebuild_player_days(self.session, pairs, settings.APP_TZ)
        await derived_repository.refresh_player_counters(self.session, players)
        await app_state_repository.bump(self.session)
        await notify(self.session, game_id, ISHAARA)
        await self.session.commit()
        logger.info("ishaara game deleted", extra={"game_id": str(game_id)})

    async def delete_owned(self, owner: User, actor: User) -> int:
        """Soft-delete every live game `owner` hosts, as `actor`, each through `delete`."""
        deleted = 0
        for game_id in await games_repository.owned_ids(self.session, owner.id, kind=KIND):
            try:
                await self.delete(game_id, actor)
            except ApiError as error:
                # Another request removed it first; the goal is the same.
                if error.status != 404:
                    raise
                continue
            deleted += 1
        return deleted

    async def reseat(self, row: Game, from_id: uuid.UUID, into_id: uuid.UUID) -> None:
        """Move a seat to another registry player (admin merge); keeps the rev."""
        state = copy.deepcopy(row.payload)
        for seat in state["players"]:
            if seat.get("playerId") == str(from_id):
                seat["playerId"] = str(into_id)
        moments = await ishaara_derived_repository.moments(self.session, row.id)
        await ishaara_repository.reseat_role(self.session, row.id, from_id, into_id)
        await ishaara_derived_repository.reseat(self.session, row.id, from_id, into_id)
        tz = settings.tz
        pairs = {(p, at.astimezone(tz).date()) for _, at in moments for p in (from_id, into_id)}
        await ishaara_derived_repository.rebuild_player_days(self.session, pairs, settings.APP_TZ)
        await self.save_state(row, state)

    async def end_idle(self, game_id: uuid.UUID) -> bool:
        """Auto-end: an unfinished game idle for 2 hours ends as abandoned. Commits."""
        row = await ishaara_repository.lock_row(self.session, game_id)
        idle_before = now_utc() - timedelta(milliseconds=IDLE_MS)
        # Re-checked under the lock: an action may have moved it on meanwhile.
        if (
            row is None
            or row.kind != KIND
            or row.deleted_at is not None
            or row.status == "finished"
            or row.updated_at >= idle_before
        ):
            return False
        key = await ishaara_repository.key(self.session, game_id)
        state = abandoned(row.payload, key, to_ms(now_utc()))
        await self.save_state(row, state, {"ended": {"winner": None, "endedBy": "abandoned"}})
        await self.finish_derived(game_id, state)
        await self.session.commit()
        return True
