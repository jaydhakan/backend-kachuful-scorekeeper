"""Every game's progression, by game and by stored kind (both Spades kinds are `spades`)."""

from app.domain.enums import GAME_OF_KIND, GameKind, ProgressionGame
from app.domain.ishaara import achievements as ishaara_achievements
from app.domain.ishaara import awards as ishaara_awards
from app.domain.kaali_teeri import achievements as kt_achievements
from app.domain.kaali_teeri import awards as kt_awards
from app.domain.spades import achievements as spades_achievements
from app.domain.spades import awards as spades_awards
from app.repositories.ishaara_progression import ishaara_progression_repository as ishaara_repo
from app.repositories.kt_progression import kt_progression_repository as kt_repo
from app.repositories.spades_progression import spades_progression_repository as spades_repo
from app.services.progression.base import GameProgression, WinRateSeasons
from app.services.progression.kachuful import KachufulProgression
from app.services.progression.rules import GameRules

SPADES = GameRules(
    game=ProgressionGame.SPADES,
    game_codes=spades_achievements.MOMENT_CODES | spades_achievements.GAME_CODES,
    career_codes=spades_achievements.CAREER_CODES,
    ladder_ids=spades_achievements.LADDER_IDS,
    min_games=spades_awards.SEASON_MIN_GAMES,
    load_game=spades_repo.game_facts,
    load_career=spades_repo.career_facts,
    load_day=spades_repo.day_facts,
    load_month=spades_repo.month_facts,
    load_tallies=spades_repo.month_tallies,
    load_first=spades_repo.first_finished,
    earn_game=spades_achievements.game_achievements,
    earn_career=spades_achievements.career_achievements,
    count_ladders=spades_achievements.ladder_counts,
    award_day=spades_awards.daily_awards,
    award_season=spades_awards.season_awards,
)

KAALI_TEERI = GameRules(
    game=ProgressionGame.KAALI_TEERI,
    game_codes=kt_achievements.MOMENT_CODES | kt_achievements.GAME_CODES,
    career_codes=kt_achievements.CAREER_CODES,
    ladder_ids=kt_achievements.LADDER_IDS,
    min_games=kt_awards.SEASON_MIN_GAMES,
    load_game=kt_repo.game_facts,
    load_career=kt_repo.career_facts,
    load_day=kt_repo.day_facts,
    load_month=kt_repo.month_facts,
    load_tallies=kt_repo.month_tallies,
    load_first=kt_repo.first_finished,
    earn_game=kt_achievements.game_achievements,
    earn_career=kt_achievements.career_achievements,
    count_ladders=kt_achievements.ladder_counts,
    award_day=kt_awards.daily_awards,
    award_season=kt_awards.season_awards,
)

ISHAARA = GameRules(
    game=ProgressionGame.ISHAARA,
    game_codes=ishaara_achievements.MOMENT_CODES | ishaara_achievements.GAME_CODES,
    career_codes=ishaara_achievements.CAREER_CODES,
    ladder_ids=ishaara_achievements.LADDER_IDS,
    min_games=ishaara_awards.SEASON_MIN_GAMES,
    load_game=ishaara_repo.game_facts,
    load_career=ishaara_repo.career_facts,
    load_day=ishaara_repo.day_facts,
    load_month=ishaara_repo.month_facts,
    load_tallies=ishaara_repo.month_tallies,
    load_first=ishaara_repo.first_finished,
    earn_game=ishaara_achievements.game_achievements,
    earn_career=ishaara_achievements.career_achievements,
    count_ladders=ishaara_achievements.ladder_counts,
    award_day=ishaara_awards.daily_awards,
    award_season=ishaara_awards.season_awards,
)

# Read at call time, so a test can swap one game's entry.
REGISTRY: dict[ProgressionGame, GameProgression] = {
    ProgressionGame.KACHUFUL: KachufulProgression(),
    ProgressionGame.SPADES: SPADES,
    ProgressionGame.KAALI_TEERI: KAALI_TEERI,
    ProgressionGame.ISHAARA: ISHAARA,
}

# Every game but Kachuful ranks its months by win rate (Kachuful keeps its points board).
SEASONS: dict[ProgressionGame, WinRateSeasons] = {
    ProgressionGame.SPADES: SPADES,
    ProgressionGame.KAALI_TEERI: KAALI_TEERI,
    ProgressionGame.ISHAARA: ISHAARA,
}


def progression_of(game: ProgressionGame) -> GameProgression:
    """A game's progression."""
    return REGISTRY[game]


def progression_of_kind(kind: GameKind) -> GameProgression:
    """The progression of the game a stored kind belongs to."""
    return REGISTRY[GAME_OF_KIND[kind]]


def seasons_of(game: ProgressionGame) -> WinRateSeasons:
    """A non-Kachuful game's win-rate seasons."""
    return SEASONS[game]
