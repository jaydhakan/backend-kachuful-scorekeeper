"""Sikke (rewards plan §9): reconciling the ledger after a write, and reading wallets and ledgers.

The reconcile mirrors `AchievementsEngine`: compute what a player should hold now, insert new keys,
update changed ones and delete keys that no longer hold; a kept row's `earned_at` never changes.
Callers run it inside their own write's transaction.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Achievement
from app.domain.crowns import CrownKind
from app.domain.enums import ProgressionGame
from app.domain.kursi import KursiGame, kursi_games
from app.domain.sikke import (
    BOUNTY_KINDS,
    KURSI_KINDS,
    PLAY_KINDS,
    PLAYER_KINDS,
    SITARA_KINDS,
    AchievementFact,
    Level,
    SikkeEarned,
    SikkeKind,
    TajReign,
    achievement_entries,
    bounty_entries,
    kursi_entries,
    level_of,
    play_entries,
    sitara_entries,
)
from app.domain.time import from_ms, to_ms
from app.exceptions import ApiError, ErrorCode
from app.repositories.boards import boards_repository
from app.repositories.crowns import crowns_repository
from app.repositories.games import games_repository
from app.repositories.nights import nights_repository
from app.repositories.players import players_repository
from app.repositories.sikke import EntryRow, StoredEntry, sikke_repository
from app.schemas.common import Page
from app.schemas.sikke import (
    GameSikkeEntryOut,
    GameSikkeOut,
    GameSikkePlayerOut,
    LevelOut,
    LevelUpOut,
    SikkeEntryOut,
    SikkeOut,
)
from app.services.pagination import decode_cursor, encode_cursor
from app.settings import settings

Key = tuple[str, str]


def _values(e: SikkeEarned) -> dict[str, Any]:
    return {
        "player_id": uuid.UUID(e.player_id),
        "key": e.key,
        "kind": e.kind.value,
        "amount": e.amount,
        "game": None if e.game is None else e.game.value,
        "game_id": None if e.game_id is None else uuid.UUID(e.game_id),
        "achievement_id": None if e.achievement_id is None else uuid.UUID(e.achievement_id),
        "earned_at": from_ms(e.earned_at),
        "data": e.data,
    }


def _changed(row: StoredEntry, e: SikkeEarned) -> bool:
    return (
        row.kind != e.kind.value
        or row.amount != e.amount
        or row.game != (None if e.game is None else e.game.value)
        or _game_id(row) != e.game_id
        or (None if row.achievement_id is None else str(row.achievement_id)) != e.achievement_id
        or row.data != e.data
    )


def _game_id(row: StoredEntry) -> str | None:
    return None if row.game_id is None else str(row.game_id)


def _achievement_entries(rows: Sequence[Achievement]) -> list[SikkeEarned]:
    """What some newly earned achievement rows pay, as `desired_for` would compute them."""
    return [
        e
        for row in rows
        for e in achievement_entries(
            str(row.player_id),
            [
                AchievementFact(
                    id=str(row.id),
                    game=ProgressionGame(row.game),
                    code=row.code,
                    game_id=None if row.game_id is None else str(row.game_id),
                    earned_at=to_ms(row.earned_at),
                )
            ],
        )
    ]


def level_out(level: Level) -> LevelOut:
    """A domain level on the wire."""
    return LevelOut(n=level.n, from_=level.from_, next=level.next)


def _entry_out(row: EntryRow) -> SikkeEntryOut:
    e = row.entry
    return SikkeEntryOut(
        id=e.id,
        kind=e.kind,
        amount=e.amount,
        game=e.game,
        game_id=e.game_id,
        code=row.code,
        period=row.period,
        earned_at=to_ms(e.earned_at),
    )


def _seat_order(summary: dict[str, Any]) -> list[uuid.UUID]:
    """A game's seated players from its stored summary (online Spades keeps `seats`)."""
    seats = summary.get("players") or summary.get("seats") or []
    ids = [s.get("playerId") for s in seats if s is not None]
    return [uuid.UUID(pid) for pid in ids if pid]


def _game_player_out(
    player_id: uuid.UUID, rows: list[EntryRow], lifetime: int
) -> GameSikkePlayerOut:
    amount = sum(r.entry.amount for r in rows)
    now, before = level_of(lifetime), level_of(lifetime - amount)
    return GameSikkePlayerOut(
        player_id=player_id,
        amount=amount,
        entries=[
            GameSikkeEntryOut(kind=r.entry.kind, amount=r.entry.amount, code=r.code) for r in rows
        ],
        level=level_out(now),
        level_up=LevelUpOut(from_=before.n, to=now.n) if before.n < now.n else None,
    )


def _not_found(what: str) -> ApiError:
    return ApiError(404, ErrorCode.NOT_FOUND, f"No such {what}.")


class SikkeService:
    """Keeps `sikke_entries` equal to the rules, and serves wallets, ledgers and game payouts."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def desired_for(self, player_ids: Iterable[uuid.UUID]) -> list[SikkeEarned]:
        """Finishes, wins, streaks and achievements these players should hold now."""
        ids = set(player_ids)
        games = await sikke_repository.finished_games(self.session, ids)
        rows = await sikke_repository.achievements(self.session, ids)
        desired: list[SikkeEarned] = []
        for pid in map(str, sorted(ids)):
            desired += play_entries(pid, games.get(pid, []), settings.tz)
            desired += achievement_entries(pid, rows.get(pid, []))
        return desired

    async def desired_kursi(self, games: list[KursiGame] | None = None) -> list[SikkeEarned]:
        """Every Kursi take and defence along Kachuful's chain, and every bounty."""
        if games is None:
            games = kursi_games(await boards_repository.ended_places(self.session, None))
        created = await crowns_repository.ended_created_at(self.session)
        bounties = bounty_entries(games, created, await self._taj_reigns(), settings.tz)
        return kursi_entries(games) + bounties

    async def _taj_reigns(self) -> list[TajReign]:
        """Every reign of the Taj (`medal1`), the current one included."""
        game = ProgressionGame.KACHUFUL.value
        reigns = [
            TajReign(tuple(map(str, r.holders)), to_ms(r.since), to_ms(r.until))
            for r in await crowns_repository.reigns_of(self.session, game, CrownKind.MEDAL1)
        ]
        now = next(
            (c for c in await crowns_repository.all(self.session, game) if c.kind == "medal1"),
            None,
        )
        if now is not None and now.holders:
            reigns.append(TajReign(tuple(map(str, now.holders)), to_ms(now.since), None))
        return reigns

    async def recompute_players(self, player_ids: Iterable[uuid.UUID]) -> None:
        """Reconcile some players' finishes, wins, streaks and achievement entries."""
        ids = set(player_ids)
        if not ids:
            return
        desired = await self.desired_for(ids)
        existing = await sikke_repository.for_players(self.session, ids, PLAYER_KINDS)
        await self._apply(desired, existing)

    async def after_write(
        self, player_ids: Iterable[uuid.UUID], earned: Sequence[Achievement], *, finished: bool
    ) -> None:
        """A game write's player entries, without replaying every achievement: the engine never
        touches a kept row and a deleted one's entry cascades, so only the new rows pay. Finishes,
        wins and streaks replay the finished games when one ended, un-ended or went (`finished`)."""
        await self._apply(_achievement_entries(earned), [])
        ids = set(player_ids)
        if not finished or not ids:
            return
        games = await sikke_repository.finished_games(self.session, ids)
        desired = [
            e
            for pid in map(str, sorted(ids))
            for e in play_entries(pid, games.get(pid, []), settings.tz)
        ]
        existing = await sikke_repository.for_players(self.session, ids, PLAY_KINDS)
        await self._apply(desired, existing)

    async def recompute_kursi(self, games: list[KursiGame] | None = None) -> list[SikkeEarned]:
        """Reconcile everyone's Kursi and bounty entries: one game's end can move the whole chain
        after it. Returns the bounties this call newly paid (or moved to another game)."""
        desired = await self.desired_kursi(games)
        existing = await sikke_repository.of_kinds(self.session, KURSI_KINDS | BOUNTY_KINDS)
        added = await self._apply(desired, existing)
        return [e for e in added if e.kind == SikkeKind.BOUNTY]

    async def recompute_sitara(
        self, nights: Sequence[tuple[str, int, list[str]]], touched: Iterable[str]
    ) -> None:
        """Reconcile the Sitara pay of some nights (id, end ms, Sitara); `touched` night ids
        lose theirs unless listed in `nights` (a night that went or changed id)."""
        desired = [e for n in nights for e in sitara_entries(*n)]
        keys = {f"sitara:{i}" for i in touched} | {f"sitara:{n[0]}" for n in nights}
        existing = await sikke_repository.by_keys(self.session, keys)
        await self._apply(desired, existing)

    async def desired_sitara(self) -> list[SikkeEarned]:
        """Every closed night's Sitara pay."""
        return [
            e
            for night_id, at, holders in await nights_repository.all_sitaras(self.session)
            for e in sitara_entries(night_id, to_ms(at), holders)
        ]

    async def recompute_all_sitara(self) -> None:
        """Reconcile everyone's Sitara pay with the stored nights (recompute-sikke)."""
        existing = await sikke_repository.of_kinds(self.session, SITARA_KINDS)
        await self._apply(await self.desired_sitara(), existing)

    async def _apply(
        self, desired: Iterable[SikkeEarned], existing: Iterable[StoredEntry]
    ) -> list[SikkeEarned]:
        """Write the difference; returns entries whose key is new or now points at another game."""
        wanted: dict[Key, SikkeEarned] = {(e.player_id, e.key): e for e in desired}
        stored: dict[Key, StoredEntry] = {(str(r.player_id), r.key): r for r in existing}
        await sikke_repository.delete_ids(
            self.session, [r.id for key, r in stored.items() if key not in wanted]
        )
        await sikke_repository.upsert(
            self.session,
            [
                _values(e)
                for key, e in wanted.items()
                if key not in stored or _changed(stored[key], e)
            ],
        )
        return [
            e
            for key, e in wanted.items()
            if key not in stored or _game_id(stored[key]) != e.game_id
        ]

    async def _kept_id(self, player_id: uuid.UUID) -> uuid.UUID:
        """The player, or the one a merged-away id was folded into; 404 when unknown."""
        player = await players_repository.get(self.session, player_id)
        if player is None:
            raise _not_found("player")
        return player.merged_into or player.id

    async def wallet(self, player_id: uuid.UUID) -> SikkeOut:
        """`GET /api/players/{id}/sikke`: balance, lifetime and level."""
        kept = await self._kept_id(player_id)
        lifetime = (await sikke_repository.lifetimes(self.session, [kept])).get(kept, 0)
        return SikkeOut(
            player_id=kept,
            balance=lifetime,
            lifetime=lifetime,
            level=level_out(level_of(lifetime)),
        )

    async def entries(
        self, player_id: uuid.UUID, *, cursor: str | None, limit: int
    ) -> Page[SikkeEntryOut]:
        """`GET /api/players/{id}/sikke/entries`: the ledger, newest first."""
        kept = await self._kept_id(player_id)
        before = None if cursor is None else _parse_cursor(cursor)
        rows = await sikke_repository.page(self.session, kept, before=before, limit=limit + 1)
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1].entry
            next_cursor = encode_cursor(last.earned_at, str(last.id))
        return Page(items=[_entry_out(r) for r in rows[:limit]], next_cursor=next_cursor)

    async def for_game(self, game_id: uuid.UUID) -> GameSikkeOut:
        """`GET /api/games/{id}/sikke`: what a game of any kind paid each player, seat order."""
        row = await games_repository.get(self.session, game_id, kind=None)
        if row is None or row.deleted_at is not None:
            raise _not_found("game")
        by_player: dict[uuid.UUID, list[EntryRow]] = defaultdict(list)
        for entry in await sikke_repository.for_game(self.session, game_id):
            by_player[entry.entry.player_id].append(entry)
        order = _ordered(_seat_order(row.summary), by_player)
        lifetimes = await sikke_repository.lifetimes(self.session, order)
        return GameSikkeOut(
            items=[_game_player_out(p, by_player.get(p, []), lifetimes.get(p, 0)) for p in order]
        )


def _ordered(seats: Sequence[uuid.UUID], paid: dict[uuid.UUID, list[EntryRow]]) -> list[uuid.UUID]:
    """Seat order, then anyone paid who no longer sits there (a replaced online seat)."""
    order = list(dict.fromkeys(seats))
    return order + sorted(p for p in paid if p not in order)


def _parse_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    at, key = decode_cursor(cursor)
    try:
        return at, uuid.UUID(key)
    except ValueError as exc:
        raise ApiError(
            422, ErrorCode.INVALID, "Bad cursor.", fields={"cursor": "not a valid cursor"}
        ) from exc
