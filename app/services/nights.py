"""Nights (rewards plan §7.2): group finished games into nights, close them, and read them.

A night is computed on read until it is closed (by `close-nights`, or `POST /api/nights/close`);
then it is stored with its story, posts one `night` feed item and pays its Sitara. A later write
to one of its games (or a game that joins it) recomputes it in place: same id while its earliest
game stands, the same feed item, the Sitara pay reconciled. Callers commit.
"""

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Night as NightRow
from app.db.models import User
from app.domain.crowns import sitara_of
from app.domain.derive import Facts, build_facts
from app.domain.enums import GameKind
from app.domain.game import game_from_payload
from app.domain.night import (
    NIGHT_FRESH_MS,
    NIGHT_GAP_MS,
    LinkGame,
    Night,
    NightGame,
    NightSeat,
    link_nights,
    night_dict,
    night_id_seed,
    night_story,
)
from app.domain.time import from_ms, now_utc, to_ms
from app.exceptions import ApiError, ErrorCode
from app.live.hub import notify_user
from app.logger import get_logger
from app.repositories.achievements import achievements_repository
from app.repositories.nights import SeatRow, nights_repository
from app.repositories.players import players_repository
from app.repositories.users import users_repository
from app.schemas.common import Page
from app.schemas.nights import NightDetailOut, NightItemOut
from app.services.crowns import player_views
from app.services.pagination import decode_cursor, encode_cursor
from app.services.presenters import player_summary_out
from app.services.sikke import SikkeService

logger = get_logger(__name__)

NIGHT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://play.vectanix.com/nights")
"""Night ids are uuid5 of the earliest game's id in this namespace."""
SITARA_MIN_TABLE = 3
"""Only Kachuful games of three or more seats count for the night's Sitara."""
CLOSE_LOOKBACK_MS = 48 * 3600 * 1000
"""`close-nights` looks this far back for games no night holds yet (`--all`: everything)."""
MAX_EXPANSIONS = 50
"""A night grows by linked games until nothing new joins; this bounds a pathological chain."""


def night_uuid(seed_game_id: str) -> uuid.UUID:
    """A night's id from its earliest game's id."""
    return uuid.uuid5(NIGHT_NAMESPACE, seed_game_id)


@dataclass(frozen=True)
class Group:
    """One night's games (ended oldest first) and their seats."""

    games: list[LinkGame]
    seats: list[SeatRow]

    @property
    def ids(self) -> list[str]:
        return [g.game_id for g in self.games]

    @property
    def id(self) -> uuid.UUID:
        return night_uuid(night_id_seed(self.games))

    @property
    def ended_at(self) -> int:
        return max(g.ended_at for g in self.games)


@dataclass(frozen=True)
class Built:
    """A night's story and what `nights` stores for it."""

    group: Group
    story: Night
    sitara: list[str]
    kinds: list[str]


def _link_games(seats: Iterable[SeatRow]) -> dict[str, LinkGame]:
    players: dict[str, set[str]] = defaultdict(set)
    times: dict[str, tuple[int, int]] = {}
    for s in seats:
        players[s.game_id].add(s.player_id)
        times[s.game_id] = (s.created_at, s.ended_at)
    return {
        gid: LinkGame(gid, times[gid][0], times[gid][1], frozenset(pids))
        for gid, pids in players.items()
    }


def _groups(seats: list[SeatRow], seeds: set[str]) -> list[Group]:
    """The nights holding any seed game, each with its seats."""
    by_game: dict[str, list[SeatRow]] = defaultdict(list)
    for s in seats:
        by_game[s.game_id].append(s)
    out = []
    for games in link_nights(list(_link_games(seats).values()), NIGHT_GAP_MS):
        if any(g.game_id in seeds for g in games):
            out.append(Group(games, [s for g in games for s in by_game[g.game_id]]))
    return out


def _span(games: Iterable[LinkGame]) -> tuple[datetime, datetime]:
    """The times a game linked to these could start or end in."""
    listed = list(games)
    since = min(g.created_at for g in listed) - NIGHT_GAP_MS
    until = max(g.ended_at for g in listed) + NIGHT_GAP_MS
    return from_ms(since), from_ms(until)


def _sitara(games: Sequence[NightGame]) -> list[str]:
    """`sitaraOf` over the night's Kachuful games of three or more, points summed."""
    counted = [g for g in games if g.kind == GameKind.KACHUFUL and len(g.seats) >= SITARA_MIN_TABLE]
    points: dict[str, int] = defaultdict(int)
    for g in counted:
        for s in g.seats:
            points[s.player_id] += s.points or 0
    return list(sitara_of(points, len(counted)))


def _night_game(game: LinkGame, seats: list[SeatRow], facts: Facts) -> NightGame:
    """A Kachuful game's seats from its facts (seat order, points); another kind's from its rows."""
    mine = [s for s in seats if s.game_id == game.game_id]
    kind = mine[0].kind
    if kind == GameKind.KACHUFUL:
        out = [
            NightSeat(p.player_id, p.won, p.points)
            for p in facts.players
            if p.game_id == game.game_id
        ]
    else:
        out = [NightSeat(s.player_id, s.won) for s in sorted(mine, key=lambda s: s.player_id)]
    return NightGame(game.game_id, kind, game.created_at, game.ended_at, tuple(out))


def _built(group: Group, facts: Facts) -> Built:
    """A group's story."""
    ids = set(group.ids)
    games = [_night_game(g, group.seats, facts) for g in group.games]
    rounds = [r for r in facts.rounds if r.game_id in ids]
    story = night_story(games, rounds)
    return Built(group, story, _sitara(games), [g.kind for g in games])


def _uuids(ids: Iterable[str]) -> list[uuid.UUID]:
    return sorted({uuid.UUID(i) for i in ids})


def _not_found() -> ApiError:
    return ApiError(404, ErrorCode.NOT_FOUND, "No such night.")


class NightsService:
    """Builds, closes and serves nights."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def groups_of(self, seeds: set[str]) -> list[Group]:
        """The nights (finished games only) holding any of `seeds`, grown until nothing joins."""
        seats = await nights_repository.seats_of(self.session, seeds)
        known: set[str] = set()
        groups: list[Group] = []
        for _ in range(MAX_EXPANSIONS):
            groups = _groups(seats, seeds)
            ids = {gid for g in groups for gid in g.ids}
            if not ids or ids == known:
                return groups
            known = ids
            players = {s.player_id for g in groups for s in g.seats}
            since, until = _span(g for grp in groups for g in grp.games)
            seats = await nights_repository.seats_near(self.session, players, since, until)
        logger.warning("night expansion stopped", extra={"games": len(known)})
        return groups

    async def build(self, groups: Sequence[Group]) -> list[Built]:
        """Each night's story: Kachuful games from their payloads, other kinds from their seats."""
        ids = {gid for g in groups for gid in g.ids}
        payloads = await nights_repository.kachuful_payloads(self.session, ids)
        games = [game_from_payload(p) for p in payloads]
        facts = build_facts(games, to_ms(now_utc()))
        return [_built(g, facts) for g in groups]

    async def after_write(self, game_ids: set[uuid.UUID]) -> None:
        """A write to some games: recompute any closed night they belong (or belonged) to."""
        await self.settle({str(g) for g in game_ids}, close_new=False, announce=False)

    async def settle(
        self,
        seeds: set[str],
        *,
        close_new: bool,
        announce: bool,
        force: bool = False,
    ) -> list[uuid.UUID]:
        """Recompute the nights holding `seeds`; with `close_new`, close those whose last game
        ended three hours ago (`force`: now). Returns the ids of nights closed by this call."""
        now = to_ms(now_utc())
        stored = {n.id: n for n in await nights_repository.overlapping(self.session, seeds)}
        seeds = seeds | {str(g) for n in stored.values() for g in n.games}
        groups = await self.groups_of(seeds)
        more = await nights_repository.overlapping(
            self.session, {gid for g in groups for gid in g.ids}
        )
        stored |= {n.id: n for n in more}
        keep: list[tuple[Group, list[NightRow]]] = []
        for group in groups:
            ids = set(group.ids)
            was = [n for n in stored.values() if ids & {str(g) for g in n.games}]
            ready = force or now - group.ended_at >= NIGHT_GAP_MS
            if was or (close_new and ready):
                keep.append((group, was))
        if not keep and not stored:
            return []
        return await self._store(keep, stored, announce=announce)

    async def _store(
        self,
        keep: list[tuple[Group, list[NightRow]]],
        stored: dict[uuid.UUID, NightRow],
        *,
        announce: bool,
    ) -> list[uuid.UUID]:
        built = await self.build([g for g, _ in keep])
        night_ids = [b.group.id for b in built]
        touched = {str(i) for i in stored} | {str(i) for i in night_ids}
        await SikkeService(self.session).recompute_sitara(
            [(str(b.group.id), b.group.ended_at, b.sitara) for b in built], touched
        )
        feed = await nights_repository.activity(self.session, touched)
        now = now_utc()
        rows, fresh = [], []
        for b, (_, was) in zip(built, keep, strict=True):
            closed_at = min((n.closed_at for n in was if n.closed_at), default=None)
            if closed_at is None:
                fresh.append(b)
            rows.append(await self._row(b, closed_at or now))
        await nights_repository.delete(self.session, [i for i in stored if i not in night_ids])
        await nights_repository.upsert(self.session, rows)
        await self._feed(built, keep, feed, fresh, announce=announce)
        return [b.group.id for b in fresh]

    async def _row(self, b: Built, closed_at: datetime) -> dict[str, Any]:
        night_id = str(b.group.id)
        sikke = await nights_repository.sikke(self.session, b.group.ids, [f"sitara:{night_id}"])
        achievements = await nights_repository.achievements(self.session, b.group.ids)
        data = night_dict(b.story) | {
            "gameKinds": b.kinds,
            "sitara": b.sitara,
            "sikke": sikke,
            "achievements": [{"playerId": p, "game": g, "code": c} for p, g, c in achievements],
        }
        return {
            "id": b.group.id,
            "started_at": from_ms(b.story.from_),
            "ended_at": from_ms(b.story.to),
            "players": _uuids(s.player_id for s in b.group.seats),
            "games": _uuids(b.group.ids),
            "data": data,
            "closed_at": closed_at,
        }

    async def _feed(
        self,
        built: list[Built],
        keep: list[tuple[Group, list[NightRow]]],
        feed: list[Any],
        fresh: list[Built],
        *,
        announce: bool,
    ) -> None:
        """One feed item per closed night: kept (re-pointed) through a recompute, new on a close."""
        by_night: dict[str, list[Any]] = defaultdict(list)
        for item in feed:
            by_night[item.data["nightId"]].append(item)
        used: set[int] = set()
        new_rows = []
        for b, (_, was) in zip(built, keep, strict=True):
            mine = [i for n in was for i in by_night.get(str(n.id), []) if i.id not in used]
            data = self._feed_data(b)
            winner = None if b.story.winner is None else uuid.UUID(b.story.winner)
            if mine:
                used.add(mine[0].id)
                await nights_repository.set_activity(
                    self.session, mine[0].id, player_id=winner, data=data
                )
            elif announce and any(b is f for f in fresh):
                new_rows.append(
                    {"game": "kachuful", "kind": "night", "player_id": winner, "data": data}
                )
        await nights_repository.delete_activity(
            self.session, [i.id for i in feed if i.id not in used]
        )
        await achievements_repository.add_activity(self.session, new_rows)
        if announce:
            for b in fresh:
                await self._tell(b)

    @staticmethod
    def _feed_data(b: Built) -> dict[str, Any]:
        return {"nightId": str(b.group.id), "games": b.group.ids, "winner": b.story.winner}

    async def _tell(self, b: Built) -> None:
        players = {uuid.UUID(s.player_id) for s in b.group.seats}
        for user_id in await users_repository.ids_for_players(self.session, players):
            await notify_user(
                self.session, user_id, "night", {"type": "night", "id": str(b.group.id)}
            )

    async def close_due(self, *, everything: bool) -> list[uuid.UUID]:
        """`close-nights`: close every night whose last game ended three hours ago.

        `everything` (`--all`) looks through all history and posts no feed items or events.
        """
        now = to_ms(now_utc())
        since = None if everything else from_ms(now - CLOSE_LOOKBACK_MS)
        seeds = await nights_repository.uncovered(
            self.session, until=from_ms(now - NIGHT_GAP_MS), since=since
        )
        if not seeds:
            return []
        return await self.settle(set(seeds), close_new=True, announce=not everything)

    async def _open_group(self, player_id: uuid.UUID) -> Group | None:
        """A player's night that no stored night holds yet, if its last game is still tonight."""
        now = to_ms(now_utc())
        seeds = await nights_repository.uncovered(
            self.session,
            until=from_ms(now),
            since=from_ms(now - NIGHT_FRESH_MS),
            player_id=player_id,
        )
        if not seeds:
            return None
        groups = await self.groups_of(set(seeds))
        if await nights_repository.overlapping(self.session, {i for g in groups for i in g.ids}):
            return None
        return max(groups, key=lambda g: g.ended_at, default=None)

    async def close_mine(self, user: User) -> NightDetailOut:
        """`POST /api/nights/close`: close the caller's open night now; 404 when there is none."""
        group = None if user.player_id is None else await self._open_group(user.player_id)
        if group is None:
            raise _not_found()
        await self.settle(set(group.ids), close_new=True, announce=True, force=True)
        await self.session.commit()
        return await self.detail(group.id)

    async def detail(self, night_id: uuid.UUID) -> NightDetailOut:
        """`GET /api/nights/{id}`: a stored night with its story."""
        row = await nights_repository.get(self.session, night_id)
        if row is None:
            raise _not_found()
        order = _ranked_ids(row)
        views = await player_views(self.session, set(order))
        return NightDetailOut(
            id=row.id,
            closed=row.closed_at is not None,
            started_at=to_ms(row.started_at),
            ended_at=to_ms(row.ended_at),
            players=[player_summary_out(views[p]) for p in order if p in views],
            data=row.data if row.closed_at is not None else None,
        )

    async def page(
        self, player_id: uuid.UUID | None, user: User, *, cursor: str | None, limit: int
    ) -> Page[NightItemOut]:
        """`GET /api/nights`: a player's nights, newest first; the open one first on page one."""
        kept = await self._kept(player_id or user.player_id)
        if kept is None:
            return Page[NightItemOut](items=[], next_cursor=None)
        before = None if cursor is None else _parse_cursor(cursor)
        ids = [kept, *await nights_repository.merged_into(self.session, kept)]
        rows = await nights_repository.page(self.session, ids, before=before, limit=limit + 1)
        items = [await self._item(r) for r in rows[:limit]]
        if cursor is None:
            open_group = await self._open_group(kept)
            if open_group is not None:
                items.insert(0, await self._open_item(open_group))
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = encode_cursor(last.ended_at, str(last.id))
        return Page[NightItemOut](items=items, next_cursor=next_cursor)

    async def _kept(self, player_id: uuid.UUID | None) -> uuid.UUID | None:
        """The player (or the one a merged-away id went into); 404 when unknown."""
        if player_id is None:
            return None
        player = await players_repository.get(self.session, player_id)
        if player is None:
            raise ApiError(404, ErrorCode.NOT_FOUND, "No such player.")
        return player.merged_into or player.id

    async def _item(self, row: NightRow) -> NightItemOut:
        order = _ranked_ids(row)
        winner = row.data.get("winner")
        views = await player_views(self.session, set(order) | ({winner} if winner else set()))
        return NightItemOut(
            id=row.id,
            closed=True,
            started_at=to_ms(row.started_at),
            ended_at=to_ms(row.ended_at),
            players=[player_summary_out(views[p]) for p in order if p in views],
            games=len(row.games),
            winner=player_summary_out(views[winner]) if winner in views else None,
        )

    async def _open_item(self, group: Group) -> NightItemOut:
        (built,) = await self.build([group])
        story = built.story
        order = [p.player_id for p in story.players]
        views = await player_views(self.session, set(order))
        winner = story.winner
        return NightItemOut(
            id=None,
            closed=False,
            started_at=story.from_,
            ended_at=story.to,
            players=[player_summary_out(views[p]) for p in order if p in views],
            games=len(group.games),
            winner=player_summary_out(views[winner]) if winner in views else None,
        )


def _ranked_ids(row: NightRow) -> list[str]:
    """A stored night's players, best night first (as its story ranks them)."""
    ranked = [p["playerId"] for p in row.data.get("players", [])]
    rest = sorted(str(p) for p in row.players if str(p) not in ranked)
    return ranked + rest


def _parse_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    at, key = decode_cursor(cursor)
    try:
        return at, uuid.UUID(key)
    except ValueError as exc:
        raise ApiError(
            422, ErrorCode.INVALID, "Bad cursor.", fields={"cursor": "not a valid cursor"}
        ) from exc
