"""Kaali Teeri game sync: upload, read, list and delete, with Kachuful's semantics.

Every upload re-derives kt_hands, kt_game_players and kt_player_day in the same transaction.
"""

import copy
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Game as GameRow
from app.db.models import User
from app.domain.enums import GameKind
from app.domain.kaali_teeri.game import KtGame, kt_game_from_payload
from app.domain.kaali_teeri.stats import (
    KtGamePlayerRow,
    KtHandRow,
    game_player_rows,
    hand_rows,
    summary,
)
from app.domain.kaali_teeri.validate import validate
from app.domain.time import from_ms, now_utc, to_ms
from app.exceptions import ApiError, ErrorCode
from app.live.hub import notify
from app.logger import get_logger
from app.repositories.app_state import app_state_repository
from app.repositories.derived import derived_repository
from app.repositories.games import SummaryRow, games_repository
from app.repositories.kt_derived import kt_derived_repository
from app.repositories.share_links import share_links_repository
from app.schemas.games import GameTombstoneOut, UploadOut
from app.schemas.kaali_teeri import KtGameDetailOut, KtGameIn, KtGamesPageOut, KtGameSummaryOut
from app.services.games import check_shape, payload_hash, resolve_players
from app.services.pagination import decode_cursor, encode_cursor
from app.services.presenters import kt_summary_out
from app.settings import settings

logger = get_logger(__name__)

KIND = GameKind.KAALI_TEERI


@dataclass(frozen=True)
class KtDerived:
    """Everything one upload writes besides the game row."""

    game: KtGame
    hands: list[KtHandRow]
    players: list[KtGamePlayerRow]

    def hand_at(self, row: KtHandRow) -> int:
        """When a hand counts: its doneAt, else the game's last change."""
        return self.game.updated_at if row.done_at is None else row.done_at

    @property
    def ended_at(self) -> int | None:
        """Finished games end at finishedAt (else their last change); Kaali Teeri never auto-ends."""
        if self.game.status != "finished":
            return None
        return self.game.finished_at or self.game.updated_at


def derive(game: KtGame) -> KtDerived:
    """Run the domain over a validated game."""
    return KtDerived(game=game, hands=hand_rows(game), players=game_player_rows(game))


def _not_found() -> ApiError:
    return ApiError(404, ErrorCode.NOT_FOUND, "No such game.")


def _invalid_game(reason: str, hand_index: int | None = None) -> ApiError:
    extra: dict[str, Any] = {"reason": reason, "handIndex": hand_index}
    return ApiError(422, ErrorCode.INVALID_GAME, f"The game failed the {reason} check.", **extra)


def _hand_rows(game_id: uuid.UUID, derived: KtDerived) -> list[dict[str, Any]]:
    return [
        {
            "game_id": game_id,
            "hand_index": h.hand_index,
            "dealer_id": uuid.UUID(h.dealer_id),
            "bidder_id": uuid.UUID(h.bidder_id),
            "bid": h.bid,
            "forced": h.forced,
            "trump": h.trump,
            "partner_ids": [uuid.UUID(p) for p in h.partner_ids],
            "slots": h.slots,
            "team_points": h.team_points,
            "made": h.made,
            "sweep": h.sweep.value,
            "scores": h.scores,
            "done_at": from_ms(derived.hand_at(h)),
        }
        for h in derived.hands
    ]


def _game_player_rows(game_id: uuid.UUID, derived: KtDerived) -> list[dict[str, Any]]:
    times = [derived.hand_at(h) for h in derived.hands]
    ended = derived.ended_at
    return [
        {
            "game_id": game_id,
            "player_id": uuid.UUID(p.player_id),
            "seat": seat,
            "points": p.points,
            "place": p.place,
            "won": p.won,
            "hands_bid": p.hands_bid,
            "bids_made": p.bids_made,
            "times_partner": p.times_partner,
            "partner_made": p.partner_made,
            "double_slots": p.double_slots,
            "sweeps": p.sweeps,
            "first_done_at": from_ms(min(times)),
            "last_done_at": from_ms(max(times)),
            "ended_at": None if ended is None else from_ms(ended),
        }
        for seat, p in enumerate(derived.players)
    ]


def _moments(derived: KtDerived) -> list[tuple[uuid.UUID, int]]:
    """(player, time) of every hand each seat played and of the game's end."""
    ids = [uuid.UUID(p.player_id) for p in derived.players]
    moments = [(pid, derived.hand_at(h)) for h in derived.hands for pid in ids]
    if derived.ended_at is not None:
        moments += [(pid, derived.ended_at) for pid in ids]
    return moments


def _apply_to_row(row: GameRow, payload: dict[str, Any], derived: KtDerived, digest: str) -> None:
    game = derived.game
    row.rev = game.rev
    row.payload = payload
    row.payload_hash = digest
    row.schema_version = payload.get("schemaVersion")
    row.status = game.status
    row.player_count = len(game.players)
    row.round_count = len(derived.hands)
    row.summary = {**summary(game), "createdAt": game.created_at, "finishedAt": game.finished_at}
    row.created_at = from_ms(game.created_at)
    row.client_updated_at = from_ms(game.updated_at)
    row.finished_at = None if game.finished_at is None else from_ms(game.finished_at)
    row.ended_at = None if derived.ended_at is None else from_ms(derived.ended_at)
    row.auto_ended = False
    row.last_done_at = from_ms(max(derived.hand_at(h) for h in derived.hands))


def _check_id(game_id: uuid.UUID, payload: dict[str, Any]) -> None:
    if payload["syncId"] != str(game_id):
        raise _invalid_game("id")


class KtGamesService:
    """Upload, read, list and delete Kaali Teeri games."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _writable_row(self, game_id: uuid.UUID, user: User) -> GameRow | None:
        """The stored row this upload may replace (None when new), after kind, gone and owner."""
        row = await games_repository.get(self.session, game_id, kind=None)
        if row is None:
            return None
        if row.kind != KIND:
            raise ApiError(409, ErrorCode.KIND_MISMATCH, "This id belongs to another game.")
        if row.deleted_at is not None:
            raise ApiError(410, ErrorCode.GONE, "This game was deleted.")
        if row.owner_id != user.id and user.role != "admin":
            raise ApiError(
                403, ErrorCode.FORBIDDEN, "Only the scorer or the admin can update this game."
            )
        return row

    async def upload(self, game_id: uuid.UUID, payload: dict[str, Any], user: User) -> UploadOut:
        """Store a game: kind, stale and idempotency checks, validation, derive."""
        check_shape(payload, KtGameIn)
        _check_id(game_id, payload)
        await games_repository.lock(self.session, game_id)
        row = await self._writable_row(game_id, user)
        if not kt_game_from_payload(payload).done_hands:
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
        game = kt_game_from_payload(stored)
        invalid = validate(game, to_ms(now_utc()))
        if invalid is not None:
            raise _invalid_game(invalid.reason.value, invalid.hand_index)
        if row is None:
            row = GameRow(id=game_id, kind=KIND, owner_id=user.id)
        derived = derive(game)
        _apply_to_row(row, stored, derived, digest)
        await self.store(row, derived)
        await self.session.commit()
        logger.info("kaali teeri game uploaded", extra={"game_id": str(game_id), "rev": game.rev})
        return UploadOut(rev=game.rev, earned=[], remap=remap or None)

    async def store(self, row: GameRow, derived: KtDerived) -> set[uuid.UUID]:
        """Save the row and rebuild everything derived from it; returns the players involved."""
        if row.kind != KIND:
            raise ApiError(409, ErrorCode.KIND_MISMATCH, "This id belongs to another game.")
        old = await kt_derived_repository.touched_moments(self.session, row.id)
        old_players = set(await kt_derived_repository.player_ids_in_game(self.session, row.id))
        row.updated_at = now_utc()
        await games_repository.save(self.session, row)
        await kt_derived_repository.replace_rows(
            self.session,
            row.id,
            _game_player_rows(row.id, derived),
            _hand_rows(row.id, derived),
        )
        tz = settings.tz
        pairs: set[tuple[uuid.UUID, date]] = {(p, at.astimezone(tz).date()) for p, at in old}
        pairs |= {(p, from_ms(ms).astimezone(tz).date()) for p, ms in _moments(derived)}
        await kt_derived_repository.rebuild_player_days(self.session, pairs, settings.APP_TZ)
        players = old_players | {uuid.UUID(p.player_id) for p in derived.players}
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
        derived = derive(kt_game_from_payload(payload))
        _apply_to_row(row, payload, derived, row.payload_hash)
        await self.store(row, derived)

    async def summary(self, game_id: uuid.UUID) -> SummaryRow:
        """A live game's summary row; 404 for unknown, deleted or other kinds' games."""
        row = (await games_repository.summaries(self.session, [game_id], kind=KIND)).get(game_id)
        if row is None or row.deleted_at is not None:
            raise _not_found()
        return row

    async def detail(self, game_id: uuid.UUID) -> KtGameDetailOut:
        """The stored payload and its summary."""
        summary_row = await self.summary(game_id)
        row = await games_repository.get(self.session, game_id, kind=KIND)
        assert row is not None
        return KtGameDetailOut(game=row.payload, summary=kt_summary_out(summary_row))

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
    ) -> KtGamesPageOut:
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
        items: list[KtGameSummaryOut | GameTombstoneOut] = [
            GameTombstoneOut(id=r.game_id, updated_at=to_ms(r.updated_at))
            if r.deleted_at is not None
            else kt_summary_out(r)
            for r in rows[:limit]
        ]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = encode_cursor(last.updated_at, str(last.game_id))
        return KtGamesPageOut(items=items, next_cursor=next_cursor)

    async def delete(self, game_id: uuid.UUID, user: User) -> set[uuid.UUID]:
        """Soft delete; derived rows go, so boards and stats no longer count it. Commits."""
        await games_repository.lock(self.session, game_id)
        row = await games_repository.get(self.session, game_id, kind=KIND)
        if row is None or row.deleted_at is not None:
            raise _not_found()
        if row.owner_id != user.id and user.role != "admin":
            raise ApiError(403, ErrorCode.FORBIDDEN, "Only the scorer or the admin can delete it.")
        old = await kt_derived_repository.touched_moments(self.session, game_id)
        players = set(await kt_derived_repository.player_ids_in_game(self.session, game_id))
        await kt_derived_repository.delete_rows(self.session, game_id)
        await games_repository.soft_delete(self.session, game_id)
        tz = settings.tz
        pairs = {(p, at.astimezone(tz).date()) for p, at in old}
        await kt_derived_repository.rebuild_player_days(self.session, pairs, settings.APP_TZ)
        await derived_repository.refresh_player_counters(self.session, players)
        await share_links_repository.revoke(self.session, game_id, ("live", "recap"))
        await notify(self.session, game_id, "revoked")
        await app_state_repository.bump(self.session)
        await self.session.commit()
        logger.info("kaali teeri game deleted", extra={"game_id": str(game_id)})
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


def _cursor_id(key: str) -> uuid.UUID:
    try:
        return uuid.UUID(key)
    except ValueError as exc:
        raise ApiError(
            422, ErrorCode.INVALID, "Bad cursor.", fields={"cursor": "not a valid cursor"}
        ) from exc
