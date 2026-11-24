"""Milestones (rewards finish §6): keep `milestones` equal to the rules after a write, and list them.

A recompute inserts new keys, rewrites moved or changed ones and deletes keys that no longer hold
(a deleted or voided game). A created one (new, or moved to another game) gets a `milestone` feed
item and a `milestone` event to every seated player's account; a moved or gone one loses its old
feed item. Callers commit.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Milestone as MilestoneRow
from app.domain.enums import GAME_OF_KIND, GameKind, ProgressionGame
from app.domain.milestones import (
    FIRST_CODES,
    GROUP_ALL_MARKS,
    GROUP_GAME_MARKS,
    TEN_PLAYERS,
    TEN_PLAYERS_KEY,
    FirstFact,
    Milestone,
    MilestoneKind,
    RankedGame,
    first_milestones,
    group_milestones,
    milestone_diff,
)
from app.domain.time import from_ms, to_ms
from app.exceptions import ApiError, ErrorCode
from app.live.hub import notify_user
from app.repositories.achievements import achievements_repository
from app.repositories.milestones import milestones_repository
from app.repositories.users import users_repository
from app.schemas.common import Page
from app.schemas.crowns import MilestoneOut
from app.services.crowns import milestone_out, player_views
from app.services.pagination import decode_cursor, encode_cursor

_KEY_BY_CODE = {(game.value, code): key for key, (game, code) in FIRST_CODES.items()}


def _ids(raw: Sequence[uuid.UUID]) -> tuple[str, ...]:
    return tuple(sorted(str(p) for p in raw))


def _stored(row: MilestoneRow) -> Milestone:
    data = row.data or {}
    return Milestone(
        key=row.key,
        kind=MilestoneKind(row.kind),
        game=None if row.game is None else ProgressionGame(row.game),
        game_id=str(row.game_id),
        players=_ids(row.players),
        at=to_ms(row.at),
        played=ProgressionGame(data.get("played", row.game or ProgressionGame.KACHUFUL)),
        n=data.get("n"),
    )


def _values(m: Milestone) -> dict[str, Any]:
    data: dict[str, Any] = {"played": m.played.value}
    if m.n is not None:
        data["n"] = m.n
    return {
        "key": m.key,
        "kind": m.kind.value,
        "game": None if m.game is None else m.game.value,
        "game_id": uuid.UUID(m.game_id),
        "players": [uuid.UUID(p) for p in m.players],
        "at": from_ms(m.at),
        "data": data,
    }


def _activity(m: Milestone) -> dict[str, Any]:
    return {
        "game": m.played.value,
        "kind": "milestone",
        "player_id": None,
        "game_id": uuid.UUID(m.game_id),
        "data": {
            "key": m.key,
            "kind": m.kind.value,
            "game": None if m.game is None else m.game.value,
            "n": m.n,
            "players": list(m.players),
        },
    }


def _ranked(row: tuple[uuid.UUID, str, datetime, list[uuid.UUID], int, int]) -> RankedGame:
    game_id, kind, ended_at, players, n_all, n_game = row
    return RankedGame(
        str(game_id), GAME_OF_KIND[GameKind(kind)], to_ms(ended_at), _ids(players), n_all, n_game
    )


def _parse_cursor(cursor: str) -> tuple[datetime, str]:
    at, key = decode_cursor(cursor)
    if not key:
        raise ApiError(
            422, ErrorCode.INVALID, "Bad cursor.", fields={"cursor": "not a valid cursor"}
        )
    return at, key


class MilestonesService:
    """Keeps `milestones` equal to the rules; serves them."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def desired(self) -> list[Milestone]:
        """Every milestone that holds now (three queries)."""
        ranked = await milestones_repository.ranked(
            self.session, all_marks=GROUP_ALL_MARKS, game_marks=GROUP_GAME_MARKS
        )
        facts = [
            FirstFact(
                _KEY_BY_CODE[(game, code)], ProgressionGame(game), str(gid), to_ms(at), _ids(p)
            )
            for game, code, gid, at, p in await milestones_repository.first_codes(
                self.session, sorted({code for _, code in FIRST_CODES.values()})
            )
            if (game, code) in _KEY_BY_CODE
        ]
        table = await milestones_repository.first_table(
            self.session, GameKind.KACHUFUL.value, TEN_PLAYERS
        )
        if table is not None:
            gid, at, players = table
            facts.append(
                FirstFact(
                    TEN_PLAYERS_KEY, ProgressionGame.KACHUFUL, str(gid), to_ms(at), _ids(players)
                )
            )
        return group_milestones(map(_ranked, ranked)) + first_milestones(facts)

    async def recompute(self, *, announce: bool = True) -> list[Milestone]:
        """Write what changed; returns the milestones created (new, or moved to another game).
        `announce` posts their feed items and stream events (off for jobs and merges)."""
        stored = {r.key: _stored(r) for r in await milestones_repository.all(self.session)}
        diff = milestone_diff(stored, await self.desired())
        moved = [m.key for m in diff.created if m.key in stored]
        await milestones_repository.delete_keys(self.session, diff.gone)
        await milestones_repository.delete_feed(self.session, [*diff.gone, *moved])
        await milestones_repository.upsert(self.session, [_values(m) for m in diff.upsert])
        if announce and diff.created:
            await achievements_repository.add_activity(
                self.session, [_activity(m) for m in diff.created]
            )
            await self._tell(diff.created)
        return diff.created

    async def _tell(self, created: Sequence[Milestone]) -> None:
        """A `milestone` event to every account seated in each created milestone's game."""
        views = await player_views(self.session, {p for m in created for p in m.players})
        for m in created:
            frame = {"milestone": milestone_out(m, views).model_dump(mode="json", by_alias=True)}
            players = {uuid.UUID(p) for p in m.players}
            for user_id in await users_repository.ids_for_players(self.session, players):
                await notify_user(self.session, user_id, "milestone", frame)

    async def page(self, *, cursor: str | None, limit: int) -> Page[MilestoneOut]:
        """`GET /api/milestones`: newest first."""
        after = None if cursor is None else _parse_cursor(cursor)
        rows = await milestones_repository.page(self.session, after=after, limit=limit + 1)
        page = [_stored(r) for r in rows[:limit]]
        views = await player_views(self.session, {p for m in page for p in m.players})
        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = encode_cursor(last.at, last.key)
        return Page[MilestoneOut](
            items=[milestone_out(m, views) for m in page], next_cursor=next_cursor
        )
