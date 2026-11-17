"""Daily awards, seasons and Wrapped (plan §6.5, §8.4-8.5)."""

import uuid

from pydantic import Field

from app.domain.enums import ProgressionGame
from app.schemas.boards import LeaderboardRowOut, PlayerStatsOut
from app.schemas.common import CamelModel
from app.schemas.players import PlayerSummaryOut


class AwardOut(CamelModel):
    """One award. `gameId`/`roundIndex` only for round awards."""

    code: str
    player: PlayerSummaryOut
    value: float
    game_id: uuid.UUID | None = None
    round_index: int | None = None


class DailyAwardsOut(CamelModel):
    """`GET /api/awards/daily`."""

    date: str = Field(..., description="YYYY-MM-DD in APP_TZ")
    final: bool = Field(..., description="False until the day is closed (00:05 the next day)")
    awards: list[AwardOut]


class SeasonOut(CamelModel):
    """`GET /api/seasons/{yyyy-mm}`."""

    season: str
    final: bool
    standings: list[LeaderboardRowOut]
    awards: list[AwardOut]


class StandingOut(CamelModel):
    """One player in a month's win-rate standings."""

    rank: int | None = Field(..., description="Null until qualified; ties share a rank")
    qualified: bool = Field(..., description="Played at least minGames finished games")
    player: PlayerSummaryOut = Field(..., description="The player")
    value: float = Field(..., description="Win rate, 0-1")
    games: int = Field(..., description="Finished games in the month")
    wins: int = Field(..., description="Games won in the month")


class GameSeasonOut(CamelModel):
    """`GET /api/seasons/{yyyy-mm}?game=` for every game but Kachuful: win-rate standings."""

    season: str = Field(..., description="YYYY-MM")
    final: bool = Field(..., description="False until the season is closed")
    game_kind: ProgressionGame = Field(..., description="spades | kaali-teeri | ishaara")
    min_games: int = Field(..., description="Finished games in the month a player needs to rank")
    standings: list[StandingOut] = Field(
        ..., description="Qualified first by win rate, then more games; then the rest"
    )
    awards: list[AwardOut] = Field(..., description="The season's awards")


class SeasonListItemOut(CamelModel):
    """One row of `GET /api/seasons`."""

    season: str
    champion: PlayerSummaryOut | None


class MonthPointsOut(CamelModel):
    """Points in the best month."""

    month: str
    points: int


class DayPointsOut(CamelModel):
    """Points on the best day."""

    day: str
    points: int


class TrumpOut(CamelModel):
    """The trump with the best accuracy (at least 10 rounds)."""

    trump: str
    accuracy: float
    rounds: int


class NemesisOut(CamelModel):
    """Who finished above this player most often."""

    player_id: uuid.UUID
    player: PlayerSummaryOut
    losses: int


class PartnerOut(CamelModel):
    """Who this player shared most tables with."""

    player_id: uuid.UUID
    player: PlayerSummaryOut
    games: int


class BiggestRoundOut(CamelModel):
    """The best single round."""

    points: int
    game_id: uuid.UUID
    round_index: int


class DayRoundsOut(CamelModel):
    """Rounds on one of the busiest days."""

    day: str
    rounds: int


class WrappedRankOut(CamelModel):
    """Rank on the year's board (average per round, more than 50 rounds)."""

    rank: int | None
    out_of: int


class WrappedOut(CamelModel):
    """`GET /api/wrapped/{year}/{playerId}` (the app's Wrapped, plus PlayerSummaries)."""

    year: int
    player_id: uuid.UUID
    player: PlayerSummaryOut
    totals: PlayerStatsOut
    best_month: MonthPointsOut | None
    best_day: DayPointsOut | None
    favourite_trump: TrumpOut | None
    nemesis: NemesisOut | None
    partner: PartnerOut | None
    biggest_round: BiggestRoundOut | None
    longest_run: int
    achievements: int = Field(..., description="Round, game and career achievements that year")
    top_days: list[DayRoundsOut]
    rank: WrappedRankOut
