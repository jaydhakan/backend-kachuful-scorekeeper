"""Spades table-mode sync (kind `spades`): upload, read, list and delete, as Kaali Teeri's.

Every upload rewrites `spades_seats` and, once finished with a result, `spades_game_players` in the
same transaction. A cancelled game is a `finished` row (the games CHECK has no cancelled) whose
summary says cancelled; it derives no player rows, so boards and stats never count it.
"""

import copy
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game as GameRow
from app.db.models import User
from app.domain.enums import GameKind
from app.domain.spades.seats import team_of
from app.domain.spades.standings import scores_of, totals
from app.domain.spades.stats import player_rows
from app.domain.spades.validate import validate
from app.domain.time import from_ms, now_utc, to_ms
from app.exceptions import ApiError, ErrorCode
from app.live.hub import notify
from app.logger import get_logger
from app.repositories.app_state import app_state_repository
from app.repositories.derived import derived_repository
from app.repositories.games import SummaryRow, games_repository
from app.repositories.share_links import share_links_repository
from app.repositories.spades_derived import spades_derived_repository
from app.schemas.games import GameTombstoneOut, UploadOut
from app.schemas.spades import SpGameDetailOut, SpGameIn, SpGamesPageOut, SpGameSummaryOut
from app.services.games import check_shape, payload_hash, resolve_players
from app.services.pagination import decode_cursor, encode_cursor
from app.services.presenters import sp_summary_out
from app.services.spades_derive import game_player_dicts

logger = get_logger(__name__)

KIND = GameKind.SPADES
_ENDED = ("finished", "cancelled")


def done_hands(game: dict[str, Any]) -> list[dict[str, Any]]:
    """The hands scored so far (phase 'done')."""
    return [h for h in game["hands"] if isinstance(h, dict) and h.get("phase") == "done"]


@dataclass(frozen=True)
class SpDerived:
    """Everything one upload writes besides the game row."""

    game: dict[str, Any]
    done: list[dict[str, Any]]

    def hand_at(self, hand: dict[str, Any]) -> int:
        """When a hand counts: its doneAt, else the game's last change."""
        done_at = hand.get("doneAt")
        return done_at if isinstance(done_at, int) else int(self.game["updatedAt"])

    @property
    def last_done(self) -> int:
        """The latest done hand's time (an uploaded game always has one)."""
        return max(self.hand_at(h) for h in self.done)

    @property
    def ended_at(self) -> int | None:
        """Finished and cancelled games end at finishedAt, else at their last change."""
        if self.game["status"] not in _ENDED:
            return None
        return int(self.game.get("finishedAt") or self.game["updatedAt"])

    @property
    def result(self) -> dict[str, Any] | None:
        """The result of a finished game; None while active and when cancelled."""
        result = self.game.get("result")
        return result if self.game["status"] == "finished" and result else None


def derive(game: dict[str, Any]) -> SpDerived:
    """The done hands of a validated game."""
    return SpDerived(game=game, done=done_hands(game))


def summary_of(derived: SpDerived) -> dict[str, Any]:
    """The stored part of the summary (owner, photos, updatedAt and live are joined on read)."""
    game = derived.game
    target = game["rules"].get("target")
    return {
        "kind": "spades",
        "createdAt": game["createdAt"],
        "finishedAt": game.get("finishedAt"),
        "status": game["status"],
        "hands": len(derived.done),
        "target": None if target is None else int(target),
        "players": [
            {
                "playerId": p.get("playerId"),
                "name": p["name"],
                "emoji": p.get("emoji", ""),
                "color": p.get("color", ""),
                "team": team_of(seat),
            }
            for seat, p in enumerate(game["players"])
        ],
        "scores": scores_of(totals(game["hands"])),
        "result": derived.result,
    }


def _seat_rows(game_id: uuid.UUID, derived: SpDerived) -> list[dict[str, Any]]:
    last = from_ms(derived.last_done)
    return [
        {
            "game_id": game_id,
            "player_id": uuid.UUID(p["playerId"]),
            "seat": seat,
            "last_done_at": last,
        }
        for seat, p in enumerate(derived.game["players"])
    ]


def _player_rows(game_id: uuid.UUID, derived: SpDerived) -> list[dict[str, Any]]:
    result, ended = derived.result, derived.ended_at
    if result is None or ended is None:
        return []
    seats = [p["playerId"] for p in derived.game["players"]]
    rows = player_rows(derived.game["hands"], seats, result)
    return game_player_dicts(game_id, KIND, rows, from_ms(ended), str(result["endedBy"]))


def _not_found() -> ApiError:
    return ApiError(404, ErrorCode.NOT_FOUND, "No such game.")


def _invalid_game(reason: str, hand_index: int | None = None) -> ApiError:
    extra: dict[str, Any] = {"reason": reason, "handIndex": hand_index}
    return ApiError(422, ErrorCode.INVALID_GAME, f"The game failed the {reason} check.", **extra)


def _kind_mismatch() -> ApiError:
    return ApiError(409, ErrorCode.KIND_MISMATCH, "This id belongs to another game.")


def _apply_to_row(row: GameRow, payload: dict[str, Any], derived: SpDerived, digest: str) -> None:
    game = derived.game
    finished = game.get("finishedAt")
    ended = derived.ended_at
    row.rev = game["rev"]
    row.payload = payload
    row.payload_hash = digest
    row.schema_version = payload.get("schemaVersion")
    row.status = "finished" if game["status"] in _ENDED else "active"
    row.player_count = len(game["players"])
    row.round_count = len(derived.done)
    row.summary = summary_of(derived)
    row.created_at = from_ms(game["createdAt"])
    row.client_updated_at = from_ms(game["updatedAt"])
    row.finished_at = None if finished is None else from_ms(finished)
    row.ended_at = None if ended is None else from_ms(ended)
    row.auto_ended = False
    row.last_done_at = from_ms(derived.last_done)


def _check_id(game_id: uuid.UUID, payload: dict[str, Any]) -> None:
    if payload["syncId"] != str(game_id):
        raise _invalid_game("id")


def _cursor_id(key: str) -> uuid.UUID:
    try:
        return uuid.UUID(key)
    except ValueError as exc:
        raise ApiError(
            422, ErrorCode.INVALID, "Bad cursor.", fields={"cursor": "not a valid cursor"}
        ) from exc


class SpadesGamesService:
    """Upload, read, list and delete table-mode Spades games."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _writable_row(self, game_id: uuid.UUID, user: User) -> GameRow | None:
        """The stored row this upload may replace (None when new), after kind, gone and owner."""
        row = await games_repository.get(self.session, game_id, kind=None)
        if row is None:
            return None
        if row.kind != KIND:
            raise _kind_mismatch()
        if row.deleted_at is not None:
            raise ApiError(410, ErrorCode.GONE, "This game was deleted.")
        if row.owner_id != user.id and user.role != "admin":
            raise ApiError(
                403, ErrorCode.FORBIDDEN, "Only the scorer or the admin can update this game."
            )
        return row

    async def upload(self, game_id: uuid.UUID, payload: dict[str, Any], user: User) -> UploadOut:
        """Store a game: kind, stale and idempotency checks, validation, derive."""
        check_shape(payload, SpGameIn)
        _check_id(game_id, payload)
        await games_repository.lock(self.session, game_id)
        row = await self._writable_row(game_id, user)
        if not done_hands(payload):
            # A game with no hand done leaves no trace: nothing stored, and an older copy goes.
            if row is not None:
                await self.delete(game_id, user)
            return UploadOut(rev=int(payload["rev"]), earned=[])
        digest = payload_hash(payload)
        if row is not None and row.payload_hash == digest:
            return UploadOut(rev=row.rev, earned=[])
        if row is not None and int(payload["rev"]) <= row.rev:
            raise ApiError(
                409, ErrorCode.STALE, "The server has a newer version.", server=row.payload
            )
        stored, remap = await resolve_players(self.session, payload)
        invalid = validate(stored, to_ms(now_utc()))
        if invalid is not None:
            raise _invalid_game(invalid["reason"], invalid["handIndex"])
        if row is None:
            row = GameRow(id=game_id, kind=KIND, owner_id=user.id)
        derived = derive(stored)
        _apply_to_row(row, stored, derived, digest)
        await self.store(row, derived)
        await self.session.commit()
        logger.info("spades game uploaded", extra={"game_id": str(game_id), "rev": row.rev})
        return UploadOut(rev=row.rev, earned=[], remap=remap or None)

    async def store(self, row: GameRow, derived: SpDerived) -> set[uuid.UUID]:
        """Save the row and rebuild everything derived from it; returns the players involved."""
        if row.kind != KIND:
            raise _kind_mismatch()
        old = set(await spades_derived_repository.seat_ids(self.session, row.id))
        row.updated_at = now_utc()
        await games_repository.save(self.session, row)
        seats = _seat_rows(row.id, derived)
        await spades_derived_repository.replace_seats(self.session, row.id, seats)
        await spades_derived_repository.replace_players(
            self.session, row.id, _player_rows(row.id, derived)
        )
        players = old | {s["player_id"] for s in seats}
        await derived_repository.refresh_player_counters(self.session, players)
        await app_state_repository.bump(self.session)
        await notify(self.session, row.id, "game")
        return players

    async def reseat(self, row: GameRow, from_id: uuid.UUID, into_id: uuid.UUID) -> None:
        """Move a seat to another registry player (admin merge); keeps the rev and hash."""
        payload = copy.deepcopy(row.payload)
        for seat in payload["players"]:
            if seat.get("playerId") == str(from_id):
                seat["playerId"] = str(into_id)
        derived = derive(payload)
        _apply_to_row(row, payload, derived, row.payload_hash)
        await self.store(row, derived)

    async def summary(self, game_id: uuid.UUID) -> SummaryRow:
        """A live game's summary row; 404 for unknown, deleted or other kinds' games."""
        row = (await games_repository.summaries(self.session, [game_id], kind=KIND)).get(game_id)
        if row is None or row.deleted_at is not None:
            raise _not_found()
        return row

    async def detail(self, game_id: uuid.UUID) -> SpGameDetailOut:
        """The stored payload and its summary."""
        summary_row = await self.summary(game_id)
        row = await games_repository.get(self.session, game_id, kind=KIND)
        assert row is not None
        return SpGameDetailOut(game=row.payload, summary=sp_summary_out(summary_row))

    async def list_page(
        self,
        *,
        updated_since: int | None,
        cursor: str | None,
        mine: bool,
        involving: bool,
        active_only: bool,
        user: User,
        limit: int,
    ) -> SpGamesPageOut:
        """Newest change first; tombstones only when `updatedSince` is given (never when active)."""
        before = None
        if cursor is not None:
            at, key = decode_cursor(cursor)
            before = (at, _cursor_id(key))
        rows = await games_repository.list_page(
            self.session,
            kind=KIND,
            updated_since=None if updated_since is None else from_ms(updated_since),
            before=before,
            owner_id=user.id if mine else None,
            active_only=active_only,
            limit=limit + 1,
            involving=(user.id, user.player_id) if involving else None,
        )
        items: list[SpGameSummaryOut | GameTombstoneOut] = [
            GameTombstoneOut(id=r.game_id, updated_at=to_ms(r.updated_at))
            if r.deleted_at is not None
            else sp_summary_out(r)
            for r in rows[:limit]
        ]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = encode_cursor(last.updated_at, str(last.game_id))
        return SpGamesPageOut(items=items, next_cursor=next_cursor)

    async def delete(self, game_id: uuid.UUID, user: User) -> set[uuid.UUID]:
        """Soft delete; derived rows go, so boards and stats no longer count it. Commits."""
        await games_repository.lock(self.session, game_id)
        row = await games_repository.get(self.session, game_id, kind=KIND)
        if row is None or row.deleted_at is not None:
            raise _not_found()
        if row.owner_id != user.id and user.role != "admin":
            raise ApiError(403, ErrorCode.FORBIDDEN, "Only the scorer or the admin can delete it.")
        players = set(await spades_derived_repository.seat_ids(self.session, game_id))
        await spades_derived_repository.delete_players(self.session, game_id)
        await spades_derived_repository.delete_seats(self.session, game_id)
        await games_repository.soft_delete(self.session, game_id)
        await derived_repository.refresh_player_counters(self.session, players)
        await share_links_repository.revoke(self.session, game_id, ("live", "recap"))
        await notify(self.session, game_id, "revoked")
        await app_state_repository.bump(self.session)
        await self.session.commit()
        logger.info("spades game deleted", extra={"game_id": str(game_id)})
        return players

    async def delete_owned(self, owner: User, actor: User) -> int:
        """Soft-delete every live game `owner` scored, as `actor`, each through `delete`."""
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
