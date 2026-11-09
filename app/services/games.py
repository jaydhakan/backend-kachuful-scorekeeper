"""Game sync: upload (PUT), read, list and delete. Every upload re-derives in one transaction."""

import copy
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement, User
from app.db.models import Game as GameRow
from app.domain.derive import (
    Ended,
    GameFact,
    GameInfo,
    RoundFact,
    game_ended_at,
    game_facts,
    round_facts,
)
from app.domain.enums import GameKind
from app.domain.game import Game, game_from_payload, sync_id
from app.domain.rules import standings
from app.domain.time import day_of, from_ms, now_utc, to_ms
from app.domain.validate import validate
from app.exceptions import ApiError, ErrorCode
from app.live.hub import notify
from app.logger import get_logger
from app.repositories.achievements import achievements_repository
from app.repositories.app_state import app_state_repository
from app.repositories.derived import derived_repository
from app.repositories.games import SummaryRow, games_repository
from app.repositories.players import players_repository
from app.repositories.share_links import share_links_repository
from app.schemas.games import (
    DeletedOut,
    GameDetailOut,
    GamePayloadIn,
    GamesPageOut,
    GameSummaryOut,
    GameTombstoneOut,
    UploadOut,
)
from app.services.achievements import AchievementsEngine
from app.services.achievements_read import achievement_out, achievements_for_game
from app.services.pagination import decode_cursor, encode_cursor
from app.services.presenters import summary_out
from app.settings import settings

logger = get_logger(__name__)

MAX_JSON_DEPTH = 12


@dataclass(frozen=True)
class Derived:
    """Everything one upload writes besides the game row."""

    game: Game
    ended: Ended
    rounds: list[RoundFact]
    players: list[GameFact]


def payload_hash(payload: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON (sorted keys, no spaces): equal payloads, equal hashes."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _depth(value: Any) -> int:
    if isinstance(value, dict):
        return 1 + max((_depth(v) for v in value.values()), default=0)
    if isinstance(value, list):
        return 1 + max((_depth(v) for v in value), default=0)
    return 0


def _kind_mismatch() -> ApiError:
    return ApiError(409, ErrorCode.KIND_MISMATCH, "This id belongs to another game.")


def _invalid_game(reason: str, round_index: int | None = None) -> ApiError:
    extra: dict[str, Any] = {"reason": reason}
    if round_index is not None:
        extra["roundIndex"] = round_index
    return ApiError(422, ErrorCode.INVALID_GAME, f"The game failed the {reason} check.", **extra)


def _check_shape(payload: dict[str, Any]) -> None:
    if _depth(payload) > MAX_JSON_DEPTH:
        raise ApiError(422, ErrorCode.INVALID, "Too deeply nested.", fields={"body": "too deep"})
    try:
        GamePayloadIn.model_validate(payload)
    except ValidationError as exc:
        fields = {".".join(str(p) for p in e["loc"]): e["msg"] for e in exc.errors()}
        raise ApiError(
            422, ErrorCode.INVALID, "The game is not well formed.", fields=fields
        ) from exc


def summary_of(game: Game, ended_at: int | None) -> dict[str, Any]:
    """The stored part of GameSummary (owner and live are joined in on read)."""
    by_seat = {s.seat.id: s for s in standings(game)}
    return {
        "createdAt": game.created_at,
        "endedAt": ended_at,
        "status": game.status,
        "players": [
            {
                "playerId": seat.player_id,
                "name": seat.name,
                "emoji": seat.emoji,
                "color": seat.color,
                "points": by_seat[seat.id].total,
                "place": by_seat[seat.id].rank if ended_at is not None else None,
            }
            for seat in game.players
        ],
        "rounds": len(game.done_rounds),
        "lengthMode": game.length_mode,
    }


def _game_player_rows(derived: Derived) -> list[dict[str, Any]]:
    placed = [p.place for p in derived.players if p.place is not None]
    worst = max(placed, default=0)
    rows = []
    for fact in derived.players:
        done = [r.done_at for r in derived.rounds if r.player_id == fact.player_id]
        rows.append(
            {
                "game_id": uuid.UUID(fact.game_id),
                "local_id": fact.local_id,
                "seat": fact.seat,
                "player_id": uuid.UUID(fact.player_id),
                "name_snapshot": fact.name,
                "emoji": fact.emoji,
                "color": fact.color,
                "points": fact.points,
                "place": fact.place,
                "won": fact.won,
                "last": worst > 1 and fact.place == worst,
                "rounds": fact.rounds,
                "made": fact.made,
                "missed": fact.missed,
                "best_round": fact.best_round,
                "worst_round": fact.worst_round,
                "longest_hot": fact.longest_hot,
                "longest_cold": fact.longest_cold,
                "hook_hits": fact.hook_hits,
                "first_done_at": from_ms(min(done)) if done else None,
                "last_done_at": from_ms(max(done)) if done else None,
                "ended_at": None if fact.ended_at is None else from_ms(fact.ended_at),
            }
        )
    return rows


def _round_score_rows(rounds: list[RoundFact]) -> list[dict[str, Any]]:
    return [
        {
            "game_id": uuid.UUID(r.game_id),
            "round_index": r.round_index,
            "player_id": uuid.UUID(r.player_id),
            "done_at": from_ms(r.done_at),
            "cards": r.cards,
            "max_cards": r.max_cards,
            "trump": r.trump,
            "bid": r.bid,
            "outcome": r.outcome,
            "points": r.points,
            "is_dealer": r.is_dealer,
            "hook_hits": r.hook_hits,
            "run_hot": r.run_hot,
            "run_cold": r.run_cold,
            "table_size": r.table_size,
            "made_count": r.made_count,
        }
        for r in rounds
    ]


@dataclass(frozen=True)
class Stored:
    """What a store touched: the players involved and achievements newly earned."""

    players: set[uuid.UUID]
    earned: list[Achievement]


def _info(derived: Derived) -> GameInfo:
    game = derived.game
    return GameInfo(
        game_id=game.id,
        created_at=game.created_at,
        ended_at=derived.ended.at,
        rounds=len(game.done_rounds),
        table_size=len(game.players),
        player_ids=tuple(p.player_id for p in derived.players),
    )


def derive(game: Game, now_ms: int) -> Derived:
    """Run the domain over a validated game."""
    ended = game_ended_at(game, now_ms)
    rounds = round_facts(game)
    return Derived(
        game=game, ended=ended, rounds=rounds, players=game_facts(game, ended.at, rounds)
    )


def _moments(derived: Derived) -> list[tuple[uuid.UUID, int]]:
    moments = [(uuid.UUID(r.player_id), r.done_at) for r in derived.rounds]
    moments += [
        (uuid.UUID(p.player_id), p.ended_at)
        for p in derived.players
        if p.ended_at is not None and p.place is not None
    ]
    return moments


def _apply_to_row(row: GameRow, payload: dict[str, Any], derived: Derived, digest: str) -> None:
    game = derived.game
    done = [r.done_at for r in derived.rounds]
    row.rev = game.rev
    row.payload = payload
    row.payload_hash = digest
    row.schema_version = payload.get("schemaVersion")
    row.status = game.status
    row.player_count = len(game.players)
    row.round_count = len(game.done_rounds)
    row.summary = summary_of(game, derived.ended.at)
    row.created_at = from_ms(game.created_at)
    row.client_updated_at = from_ms(game.updated_at)
    row.finished_at = None if game.finished_at is None else from_ms(game.finished_at)
    row.ended_at = None if derived.ended.at is None else from_ms(derived.ended.at)
    row.auto_ended = derived.ended.auto
    row.last_done_at = from_ms(max(done)) if done else None


class GamesService:
    """Upload, read, list and delete games."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _resolve_players(
        self, payload: dict[str, Any]
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Check every seat's registry id; rewrite merged ids to their target."""
        seats = payload["players"]
        raw_ids = [seat.get("playerId") for seat in seats]
        if any(not pid for pid in raw_ids):
            raise _invalid_game("players")
        parsed: dict[str, uuid.UUID] = {}
        for pid in raw_ids:
            try:
                parsed[pid] = uuid.UUID(pid)
            except ValueError:
                continue
        found = await players_repository.get_many(self.session, list(parsed.values()))
        unknown = [pid for pid in raw_ids if pid not in parsed or parsed[pid] not in found]
        if unknown:
            raise ApiError(
                422,
                ErrorCode.UNKNOWN_PLAYER,
                "Some players are not in the registry yet.",
                playerIds=unknown,
            )
        remap = {
            pid: str(found[parsed[pid]].merged_into)
            for pid in raw_ids
            if found[parsed[pid]].merged_into is not None
        }
        if not remap:
            return payload, {}
        rewritten = copy.deepcopy(payload)
        for seat in rewritten["players"]:
            seat["playerId"] = remap.get(seat["playerId"], seat["playerId"])
        return rewritten, remap

    async def upload(self, game_id: uuid.UUID, payload: dict[str, Any], user: User) -> UploadOut:
        """Store a game from the app: stale and idempotency checks, validation, derive."""
        _check_shape(payload)
        if sync_id(payload) != str(game_id):
            raise _invalid_game("id")
        await games_repository.lock(self.session, game_id)
        row = await games_repository.get(self.session, game_id, kind=None)
        if row is not None and row.kind != GameKind.KACHUFUL:
            raise _kind_mismatch()
        if row is not None and row.deleted_at is not None:
            raise ApiError(410, ErrorCode.GONE, "This game was deleted.")
        if row is not None and row.owner_id != user.id and user.role != "admin":
            raise ApiError(
                403, ErrorCode.FORBIDDEN, "Only the scorer or the admin can update this game."
            )
        if not game_from_payload(payload).done_rounds:
            # A game with no round done leaves no trace: nothing stored, and an older copy goes.
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
        stored, remap = await self._resolve_players(payload)
        game = game_from_payload(stored)
        now_ms = to_ms(now_utc())
        validity = validate(game, now_ms)
        if not validity.ok:
            assert validity.reason is not None
            raise _invalid_game(validity.reason.value, validity.round_index)
        derived = derive(game, now_ms)
        if row is None:
            row = GameRow(id=game_id, kind=GameKind.KACHUFUL, owner_id=user.id)
        was_ended = row.ended_at is not None
        _apply_to_row(row, stored, derived, digest)
        result = await self.store(row, derived, was_ended=was_ended)
        await self.session.commit()
        logger.info("game uploaded", extra={"game_id": str(game_id), "rev": game.rev})
        return UploadOut(
            rev=game.rev,
            earned=[achievement_out(a) for a in result.earned],
            remap=remap or None,
        )

    async def store(
        self, row: GameRow, derived: Derived, *, was_ended: bool, career: bool = True
    ) -> Stored:
        """Save the row and rebuild everything derived from it, achievements included."""
        # The derived tables, boards and achievements hold Kachuful games only.
        if row.kind != GameKind.KACHUFUL:
            raise _kind_mismatch()
        old = await derived_repository.touched_moments(self.session, row.id)
        old_players = set(await derived_repository.player_ids_in_game(self.session, row.id))
        row.updated_at = now_utc()
        await games_repository.save(self.session, row)
        await derived_repository.replace_rows(
            self.session, row.id, _game_player_rows(derived), _round_score_rows(derived.rounds)
        )
        tz = settings.tz
        pairs: set[tuple[uuid.UUID, date]] = {(p, at.astimezone(tz).date()) for p, at in old}
        pairs |= {(p, day_of(ms, tz)) for p, ms in _moments(derived)}
        await derived_repository.rebuild_player_days(self.session, pairs, settings.APP_TZ)
        players = old_players | {uuid.UUID(p.player_id) for p in derived.players}
        await derived_repository.refresh_player_counters(self.session, players)
        engine = AchievementsEngine(self.session)
        earned = await engine.game(row.id, derived.rounds, derived.players, _info(derived))
        moments = [to_ms(at) for _, at in old] + [ms for _, ms in _moments(derived)]
        ended = derived.ended.at is not None
        if career and moments:
            earned += await engine.career(
                players, changed_from_ms=min(moments), sequence_changed=was_ended or ended
            )
        if ended and not was_ended:
            await achievements_repository.add_activity(
                self.session, [{"kind": "game_finished", "game_id": row.id, "data": {}}]
            )
        elif was_ended and not ended:
            await achievements_repository.delete_activity(self.session, row.id, "game_finished")
        await app_state_repository.bump(self.session)
        await notify(self.session, row.id, "game")
        return Stored(players=players, earned=earned)

    async def rederive(
        self, row: GameRow, payload: dict[str, Any], *, career: bool = True
    ) -> Derived:
        """Re-run the domain over a stored game (merge, auto-end); keeps its rev and hash."""
        derived = derive(game_from_payload(payload), to_ms(now_utc()))
        was_ended = row.ended_at is not None
        _apply_to_row(row, payload, derived, row.payload_hash)
        await self.store(row, derived, was_ended=was_ended, career=career)
        return derived

    async def reseat(self, row: GameRow, from_id: uuid.UUID, into_id: uuid.UUID) -> None:
        """Move a seat to another registry player (admin merge); career badges are the caller's."""
        payload = copy.deepcopy(row.payload)
        for seat in payload["players"]:
            if seat.get("playerId") == str(from_id):
                seat["playerId"] = str(into_id)
        await self.rederive(row, payload, career=False)

    async def summary(self, game_id: uuid.UUID) -> SummaryRow:
        """A live game's summary row; 404 for unknown or deleted games."""
        rows = await games_repository.summaries(self.session, [game_id], kind=GameKind.KACHUFUL)
        row = rows.get(game_id)
        if row is None or row.deleted_at is not None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such game.")
        return row

    async def detail(self, game_id: uuid.UUID) -> GameDetailOut:
        """The stored payload, its summary and its achievements."""
        summary = await self.summary(game_id)
        row = await games_repository.get(self.session, game_id, kind=GameKind.KACHUFUL)
        assert row is not None
        return GameDetailOut(
            game=row.payload,
            summary=summary_out(summary),
            achievements=await achievements_for_game(self.session, game_id),
        )

    async def list_page(
        self,
        *,
        updated_since: int | None,
        cursor: str | None,
        mine: bool,
        active_only: bool,
        user: User,
        limit: int,
        involving: bool = False,
    ) -> GamesPageOut:
        """Newest change first; tombstones only when `updatedSince` is given (never when active)."""
        before = None
        if cursor is not None:
            at, key = decode_cursor(cursor)
            try:
                before = (at, uuid.UUID(key))
            except ValueError as exc:
                raise ApiError(
                    422, ErrorCode.INVALID, "Bad cursor.", fields={"cursor": "not a valid cursor"}
                ) from exc
        rows = await games_repository.list_page(
            self.session,
            kind=GameKind.KACHUFUL,
            updated_since=None if updated_since is None else from_ms(updated_since),
            before=before,
            owner_id=user.id if mine else None,
            active_only=active_only,
            limit=limit + 1,
            involving=(user.id, user.player_id) if involving else None,
        )
        items: list[GameSummaryOut | GameTombstoneOut] = [
            GameTombstoneOut(id=r.game_id, updated_at=to_ms(r.updated_at))
            if r.deleted_at is not None
            else summary_out(r)
            for r in rows[:limit]
        ]
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = encode_cursor(last.updated_at, str(last.game_id))
        return GamesPageOut(items=items, next_cursor=next_cursor)

    async def delete(self, game_id: uuid.UUID, user: User) -> set[uuid.UUID]:
        """Soft delete; derived rows go, so boards and stats no longer count it."""
        await games_repository.lock(self.session, game_id)
        row = await games_repository.get(self.session, game_id, kind=GameKind.KACHUFUL)
        if row is None or row.deleted_at is not None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such game.")
        if row.owner_id != user.id and user.role != "admin":
            raise ApiError(403, ErrorCode.FORBIDDEN, "Only the scorer or the admin can delete it.")
        old = await derived_repository.touched_moments(self.session, game_id)
        players = set(await derived_repository.player_ids_in_game(self.session, game_id))
        await derived_repository.delete_rows(self.session, game_id)
        await games_repository.soft_delete(self.session, game_id)
        tz = settings.tz
        pairs = {(p, at.astimezone(tz).date()) for p, at in old}
        await derived_repository.rebuild_player_days(self.session, pairs, settings.APP_TZ)
        await derived_repository.refresh_player_counters(self.session, players)
        engine = AchievementsEngine(self.session)
        await engine.game(game_id, [], [], None)
        if old:
            await engine.career(
                players,
                changed_from_ms=min(to_ms(at) for _, at in old),
                sequence_changed=row.ended_at is not None,
            )
        await achievements_repository.delete_activity(self.session, game_id, "game_finished")
        await share_links_repository.revoke(self.session, game_id, ("live", "recap"))
        await notify(self.session, game_id, "revoked")
        await app_state_repository.bump(self.session)
        await self.session.commit()
        logger.info("game deleted", extra={"game_id": str(game_id)})
        return players

    async def start_fresh(self, user: User) -> DeletedOut:
        """Delete every game the user scored, then the players they added that nothing else uses."""
        games = await self._delete_owned(user)
        players = await players_repository.delete_unused_created_by(self.session, user.id)
        if players:
            await app_state_repository.bump(self.session)
        await self.session.commit()
        logger.info("start fresh", extra={"games": games, "players": players})
        return DeletedOut(games=games, players=players)

    async def delete_owned(self, owner: User, actor: User) -> int:
        """Soft-delete every live game `owner` scored, as `actor` (the owner, or an admin); commits."""
        games = await self._delete_owned(owner, actor)
        await self.session.commit()
        return games

    async def _delete_owned(self, user: User, actor: User | None = None) -> int:
        """Each game goes through `delete`, so a single use case defines what deleting one means."""
        deleted = 0
        for game_id in await games_repository.owned_ids(
            self.session, user.id, kind=GameKind.KACHUFUL
        ):
            try:
                await self.delete(game_id, actor or user)
            except ApiError as error:
                # Another request removed it first; the goal is the same.
                if error.status != 404:
                    raise
                continue
            deleted += 1
        return deleted
