"""The next game of an Ishaara match: same seats and rules, fresh words, clue-givers rotated."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.domain.ishaara.types import TEAMS, State, other
from app.domain.time import now_utc, to_ms
from app.exceptions import ApiError, ErrorCode
from app.logger import get_logger
from app.repositories.games import games_repository
from app.repositories.ishaara import CLUE_GIVER, ishaara_repository
from app.schemas.ishaara import ClueGiversIn, IshaaraGameOut, RematchIn
from app.services.ishaara_games import IshaaraGamesService
from app.services.ishaara_state import Access, game_out, id_conflict, invalid_game, not_found

logger = get_logger(__name__)


def next_match(state: State) -> dict[str, int]:
    """Best of 3 with neither team on 2 wins goes on; anything else starts a new match."""
    m = state["match"]
    if state["rules"]["bestOf"] == 3 and m["sunWins"] < 2 and m["moonWins"] < 2:
        return {"game": m["game"] + 1, "sunWins": m["sunWins"], "moonWins": m["moonWins"]}
    return {"game": 1, "sunWins": 0, "moonWins": 0}


def _rotated(team_seats: list[dict[str, Any]], linked: set[str]) -> str:
    """The next seat after the current clue-giver (seat order, wrapping) with an account."""
    current = next(i for i, s in enumerate(team_seats) if s["role"] == CLUE_GIVER)
    n = len(team_seats)
    for step in range(1, n + 1):
        candidate: str = team_seats[(current + step) % n]["playerId"]
        if candidate in linked:
            return candidate
    chosen: str = team_seats[current]["playerId"]
    return chosen


def _chosen(
    team: str, team_seats: list[dict[str, Any]], picks: ClueGiversIn | None, linked: set[str]
) -> str:
    pick = None if picks is None else getattr(picks, team)
    if pick is None:
        return _rotated(team_seats, linked)
    if str(pick) not in {s["playerId"] for s in team_seats}:
        raise invalid_game("clue-givers")
    return str(pick)


def rematch_seats(
    state: State, picks: ClueGiversIn | None, linked: set[str]
) -> list[dict[str, Any]]:
    """The same seats with each team's clue-giver for the next game."""
    givers = {
        team: _chosen(team, [p for p in state["players"] if p["team"] == team], picks, linked)
        for team in TEAMS
    }
    return [
        {**p, "role": CLUE_GIVER if givers[p["team"]] == p["playerId"] else "guesser"}
        for p in state["players"]
    ]


class IshaaraRematchService:
    """`POST /api/ishaara/games/{id}/rematch`."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._games = IshaaraGamesService(session)

    async def rematch(
        self, game_id: uuid.UUID, body: RematchIn, user: User
    ) -> tuple[IshaaraGameOut, bool]:
        """(next game, created): an existing rematch comes back as is."""
        row = await self._games.locked(game_id)
        _, access = await self._games.seen_by(row, user)
        if not access.host:
            raise ApiError(403, ErrorCode.FORBIDDEN, "Only the host starts the rematch.")
        state = row.payload
        if state["status"] != "finished":
            game = game_out(state, access).model_dump(mode="json")
            raise ApiError(
                422,
                ErrorCode.INVALID_ACTION,
                "The game isn't over.",
                reason="not-finished",
                game=game,
            )
        if state.get("next"):
            return await self._existing(uuid.UUID(state["next"]), user), False
        await games_repository.lock(self.session, body.id)
        if await games_repository.get(self.session, body.id, kind=None) is not None:
            raise id_conflict()
        accounts = await ishaara_repository.accounts_of(
            self.session, [uuid.UUID(p["playerId"]) for p in state["players"]]
        )
        seats = rematch_seats(state, body.clue_givers, {str(p) for p in accounts})
        fresh = await self._games.start(
            game_id=body.id,
            seats=seats,
            words=body.words,
            rules=state["rules"],
            owner=user,
            starting=other(state["starting"]),
            match=next_match(state),
        )
        now = to_ms(now_utc())
        await self._games.save_state(
            row, {**state, "next": str(body.id), "rev": row.rev + 1, "updatedAt": now}
        )
        await self.session.commit()
        logger.info("ishaara rematch", extra={"game_id": str(game_id), "next": str(body.id)})
        team = next(
            (
                s["team"]
                for s in seats
                if s["role"] == CLUE_GIVER and accounts.get(uuid.UUID(s["playerId"])) == user.id
            ),
            None,
        )
        return game_out(fresh, Access(host=True, team=team, member=True)), True

    async def _existing(self, game_id: uuid.UUID, user: User) -> IshaaraGameOut:
        row = await ishaara_repository.get_row(self.session, game_id)
        if row is None or row.deleted_at is not None:
            raise not_found()
        _, access = await self._games.seen_by(row, user)
        return game_out(row.payload, access)
