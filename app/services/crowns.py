"""Crowns (rewards plan §6): recompute who holds each Kachuful title after a write, and read them.

A recompute diffs the rules' holders with `crowns`: a change closes the old reign, writes the new
row and posts a `crown` feed item (not for a clear). A reign starts when a recompute first sees
its holders. Callers commit.
"""

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Crown as CrownRow
from app.domain.awards import SEASON_MIN_ROUNDS
from app.domain.crowns import (
    Crown,
    CrownChange,
    CrownKind,
    ReignSort,
    crown_changes,
    legend_of,
    medal_holders,
    sitara_of,
)
from app.domain.enums import ProgressionGame
from app.domain.leaderboard import Metric, leaderboard
from app.domain.sikke import SikkeEarned
from app.domain.time import (
    day_of,
    day_range_ms,
    from_ms,
    month_of,
    month_range_ms,
    now_utc,
    to_ms,
)
from app.exceptions import ApiError, ErrorCode
from app.repositories.achievements import achievements_repository
from app.repositories.boards import boards_repository
from app.repositories.crowns import crowns_repository
from app.repositories.players import PlayerView, players_repository
from app.schemas.common import Page
from app.schemas.crowns import (
    BountyOut,
    CrownOut,
    CrownReignOut,
    CrownsOut,
    StatusChangeOut,
    StatusOut,
)
from app.schemas.players import PlayerSummaryOut
from app.services.boards import make_window
from app.services.pagination import decode_cursor, encode_cursor
from app.services.presenters import player_summary_out
from app.settings import settings

GAME = ProgressionGame.KACHUFUL.value


def _ids(raw: Iterable[uuid.UUID]) -> tuple[str, ...]:
    return tuple(sorted(str(h) for h in raw))


def _stored(row: CrownRow) -> Crown:
    return Crown(
        CrownKind(row.kind),
        _ids(row.holders),
        to_ms(row.since),
        None if row.since_game is None else str(row.since_game),
        row.defences,
    )


def _uuid(raw: str | None) -> uuid.UUID | None:
    return None if raw is None else uuid.UUID(raw)


def _row(crown: Crown, now: int, game_id: uuid.UUID | None) -> dict[str, Any]:
    """A title as a `crowns` row, held since this write."""
    return {
        "game": GAME,
        "kind": crown.kind.value,
        "holders": [uuid.UUID(h) for h in crown.holders],
        "since": from_ms(now),
        "since_game": game_id,
        "defences": crown.defences,
    }


def _reign_row(have: Crown, now: int, game_id: uuid.UUID | None) -> dict[str, Any]:
    return {
        "game": GAME,
        "kind": have.kind.value,
        "holders": [uuid.UUID(h) for h in have.holders],
        "since": from_ms(have.since if have.since is not None else now),
        "until": from_ms(now),
        "since_game": _uuid(have.since_game),
        "until_game": game_id,
        "defences": have.defences,
    }


def _activity(change: CrownChange, game_id: uuid.UUID | None) -> dict[str, Any]:
    return {
        "game": GAME,
        "kind": "crown",
        "player_id": uuid.UUID(change.to[0]),
        "game_id": game_id,
        "data": {
            "crown": change.kind.value,
            "from": list(change.from_),
            "to": list(change.to),
            "defences": change.defences,
        },
    }


def _bad_cursor() -> ApiError:
    return ApiError(422, ErrorCode.INVALID, "Bad cursor.", fields={"cursor": "not a valid cursor"})


def _parse_cursor(cursor: str) -> tuple[int, datetime, uuid.UUID]:
    at, key = decode_cursor(cursor)
    defences, _, reign_id = key.partition(":")
    try:
        return int(defences), at, uuid.UUID(reign_id)
    except ValueError as exc:
        raise _bad_cursor() from exc


class CrownsService:
    """Keeps `crowns` and `crown_reigns` equal to the rules; serves them and a write's status."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def desired(self) -> dict[CrownKind, Crown]:
        """Who should hold each title now (since and game are the write's)."""
        now = to_ms(now_utc())
        start, end = month_range_ms(month_of(now, settings.tz), settings.tz)
        month = await boards_repository.tallies(self.session, make_window(start, end))
        board = leaderboard(month.values(), None, Metric.AVG_PER_ROUND, SEASON_MIN_ROUNDS)
        holders = medal_holders(board)
        holders[CrownKind.SITARA] = await self._sitara(now)
        holders[CrownKind.LEGEND] = legend_of(
            await crowns_repository.season_champions(self.session)
        )
        return {kind: Crown(kind, ids) for kind, ids in holders.items()}

    async def _sitara(self, now: int) -> tuple[str, ...]:
        """Today's points leaders, as `GET /api/medals` `today` counts them."""
        start, end = day_range_ms(day_of(now, settings.tz), settings.tz)
        day = await boards_repository.tallies(self.session, make_window(start, end))
        games = await boards_repository.games_on(self.session, from_ms(start), from_ms(end), None)
        return sitara_of({t.player_id: t.points for t in day.values() if t.rounds > 0}, games)

    async def recompute(
        self, *, game_id: uuid.UUID | None, announce: bool = True
    ) -> list[CrownChange]:
        """Diff every title with the stored row and write what changed (nothing when nothing did).

        `game_id` is the write's game (null for jobs and merges); `announce` posts feed items.
        """
        stored = {
            CrownKind(r.kind): _stored(r) for r in await crowns_repository.all(self.session, GAME)
        }
        desired = await self.desired()
        changes = crown_changes(stored, desired)
        now = to_ms(now_utc())
        rows, reigns, feed = [], [], []
        for change in changes:
            have = stored.get(change.kind)
            if have is not None and have.holders:
                reigns.append(_reign_row(have, now, game_id))
            rows.append(_row(desired[change.kind], now, game_id))
            if change.to and announce:
                feed.append(_activity(change, game_id))
        await crowns_repository.upsert(self.session, rows)
        await crowns_repository.add_reigns(self.session, reigns)
        await achievements_repository.add_activity(self.session, feed)
        return changes

    async def status(
        self, changes: Sequence[CrownChange], bounties: Sequence[SikkeEarned]
    ) -> StatusOut:
        """A write's changes and bounties with players filled in (no query when both are empty)."""
        ids = {h for c in changes for h in (*c.from_, *c.to)}
        ids |= {b.player_id for b in bounties} | {str(b.data["holder"]) for b in bounties}
        views = await player_views(self.session, ids)
        return StatusOut(
            changes=[
                StatusChangeOut(
                    kind=c.kind.value,
                    from_=_summaries(views, c.from_),
                    to=_summaries(views, c.to),
                    defences=c.defences,
                )
                for c in changes
            ],
            bounties=[
                BountyOut(
                    holder=player_summary_out(views[str(b.data["holder"])]),
                    winner=player_summary_out(views[b.player_id]),
                    amount=b.amount,
                )
                for b in bounties
            ],
        )

    async def crowns(self, game: ProgressionGame) -> CrownsOut:
        """`GET /api/crowns`: every title in `CrownKind` order; nobody's when never held."""
        stored = {r.kind: r for r in await crowns_repository.all(self.session, game.value)}
        views = await player_views(
            self.session, {str(h) for r in stored.values() for h in r.holders}
        )
        items = []
        for kind in CrownKind:
            row = stored.get(kind.value)
            items.append(
                CrownOut(
                    kind=kind.value,
                    holders=[] if row is None else _summaries(views, _ids(row.holders)),
                    since=None if row is None else to_ms(row.since),
                    since_game=None if row is None else row.since_game,
                    defences=0 if row is None else row.defences,
                )
            )
        return CrownsOut(items=items)

    async def reigns(
        self,
        game: ProgressionGame,
        *,
        kind: CrownKind | None,
        sort: ReignSort,
        cursor: str | None,
        limit: int,
    ) -> Page[CrownReignOut]:
        """`GET /api/crowns/reigns`: closed reigns, newest end (or most defences) first."""
        after = None if cursor is None else _parse_cursor(cursor)
        rows = await crowns_repository.page(
            self.session,
            game=game.value,
            kind=None if kind is None else kind.value,
            sort=sort,
            after=after,
            limit=limit + 1,
        )
        page = rows[:limit]
        views = await player_views(self.session, {str(h) for r in page for h in r.holders})
        next_cursor = None
        if len(rows) > limit:
            last = page[-1]
            next_cursor = encode_cursor(last.until, f"{last.defences}:{last.id}")
        return Page[CrownReignOut](
            items=[
                CrownReignOut(
                    kind=r.kind,
                    holders=_summaries(views, _ids(r.holders)),
                    since=to_ms(r.since),
                    until=to_ms(r.until),
                    defences=r.defences,
                )
                for r in page
            ],
            next_cursor=next_cursor,
        )


def _summaries(views: dict[str, PlayerView], ids: Iterable[str]) -> list[PlayerSummaryOut]:
    return [player_summary_out(views[i]) for i in ids if i in views]


async def player_views(session: AsyncSession, ids: set[str]) -> dict[str, PlayerView]:
    """Players by id, following merges to the player that remains (two queries at most)."""
    if not ids:
        return {}
    views = await players_repository.get_views(session, [uuid.UUID(i) for i in ids])
    targets = [v.player.merged_into for v in views.values() if v.player.merged_into]
    merged = await players_repository.get_views(session, [t for t in targets if t])
    return {
        str(pid): merged.get(v.player.merged_into, v) if v.player.merged_into else v
        for pid, v in views.items()
    }
