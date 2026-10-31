"""Boards, stats and bootstrap models (plan §6.1, §6.3, §6.5)."""

import uuid

from pydantic import Field

from app.schemas.auth import UserOut
from app.schemas.common import CamelModel
from app.schemas.games import AchievementOut, GameSummaryOut
from app.schemas.players import PlayerOut, PlayerSummaryOut


class LeaderboardRowOut(CamelModel):
    """One player on a board."""

    rank: int | None = Field(..., description="Null when unqualified on a rate metric")
    qualified: bool = Field(..., description="rounds > minRounds (always true for counts)")
    player: PlayerSummaryOut
    value: float = Field(..., description="Exact value of the metric; round it for display")
    points: int
    games: int = Field(..., description="Games with a round in the range")
    rounds: int
    wins: int = Field(..., description="Games won that ended in the range")
    accuracy: float = Field(..., description="made / rounds, 0-1")
    longest_hot: int
    rank_delta: int | None = Field(
        ..., description="Previous rank minus this rank (positive = up); null if unranked"
    )


class LeaderboardOut(CamelModel):
    """`GET /api/leaderboard`."""

    rows: list[LeaderboardRowOut]
    min_rounds: int
    generated_at: int = Field(..., description="Epoch ms")


class MadeOfOut(CamelModel):
    """made out of total."""

    made: int
    total: int


class AsDealerOut(CamelModel):
    """Rounds as dealer and bids made in them."""

    rounds: int
    made: int


class PlayerStatsOut(CamelModel):
    """The contract's PlayerStats."""

    points: int
    rounds: int
    made: int
    missed: int
    accuracy: float = Field(..., description="0-1")
    avg_per_round: float
    games: int
    games_ended: int
    wins: int
    win_rate: float = Field(..., description="0-1, of games ended in the range")
    places: dict[str, int] = Field(..., description='{"1": n, "2": n, "3": n}')
    best_round: int | None
    worst_round: int | None
    longest_hot: int
    longest_cold: int
    hook_hits: int
    best_game: GameSummaryOut | None
    bid_histogram: dict[str, MadeOfOut]
    by_cards: dict[str, MadeOfOut]
    by_trump: dict[str, MadeOfOut]
    as_dealer: AsDealerOut


class RankOut(CamelModel):
    """All-time rank on the default board."""

    global_rank: int | None = Field(..., alias="global", description="Null when unranked")
    out_of: int = Field(..., description="Players ranked")


class PlayerPageOut(CamelModel):
    """`GET /api/players/{id}`."""

    player: PlayerSummaryOut
    totals: PlayerStatsOut
    achievements: list[AchievementOut]
    rank: RankOut


class CalendarOut(CamelModel):
    """`GET /api/players/{id}/calendar`: rounds per APP_TZ day."""

    days: dict[str, int]


class BiggestWinOut(CamelModel):
    """The widest gap between the two in one shared game."""

    by: int
    game: GameSummaryOut
    winner: uuid.UUID = Field(..., description="Who finished ahead in that game")


class HeadToHeadOut(CamelModel):
    """The contract's HeadToHead (over games both finished in the range)."""

    a: PlayerSummaryOut
    b: PlayerSummaryOut
    games: int
    a_ahead: int
    b_ahead: int
    ties: int
    a_points: int
    b_points: int
    last_games: list[GameSummaryOut]
    biggest_win: BiggestWinOut | None


class BootstrapOut(CamelModel):
    """`GET /api/bootstrap`: everything the app needs after sign-in, in one round trip."""

    user: UserOut
    player: PlayerOut | None
    players: list[PlayerOut]
    my_games: list[GameSummaryOut]
    my_achievements: list[AchievementOut]
    data_version: int


class ActivityItemOut(CamelModel):
    """One feed item."""

    id: int
    kind: str = Field(
        ..., description="achievement | game_finished | season_closed | player_joined"
    )
    player: PlayerSummaryOut | None
    game: GameSummaryOut | None
    data: dict[str, object]
    created_at: int = Field(..., description="Epoch ms")
