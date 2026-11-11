"""Ishaara rules, clue checks, validation, stats and boards (app: src/games/ishaara/domain). Pure; no I/O."""

from app.domain.ishaara.boards import MIN_GAMES, SORTS, head_to_head, leaderboard, tallies
from app.domain.ishaara.clue import MAX_CLUE_LEN, clue_allowed, clue_format_ok, guesses_for, norm
from app.domain.ishaara.deal import deal_key
from app.domain.ishaara.rules import (
    MIN_TEAM,
    SKIP_AFTER_MS,
    UNDO_MS,
    apply_action,
    clue_giver_of,
    join,
    new_game,
    settle,
    words_left,
    words_of,
)
from app.domain.ishaara.stats import player_rows, revealed_count
from app.domain.ishaara.types import (
    BLANKS,
    GRID,
    TABLE,
    TEAMS,
    ActionResult,
    Actor,
    Identity,
    State,
    Team,
    other,
)
from app.domain.ishaara.validate import MAX_PLAYERS, MAX_WORD_LEN, board_word_ok, validate_new

__all__ = [
    "BLANKS",
    "GRID",
    "MAX_CLUE_LEN",
    "MAX_PLAYERS",
    "MAX_WORD_LEN",
    "MIN_GAMES",
    "MIN_TEAM",
    "SKIP_AFTER_MS",
    "SORTS",
    "TABLE",
    "TEAMS",
    "UNDO_MS",
    "ActionResult",
    "Actor",
    "Identity",
    "State",
    "Team",
    "apply_action",
    "board_word_ok",
    "clue_allowed",
    "clue_format_ok",
    "clue_giver_of",
    "deal_key",
    "guesses_for",
    "head_to_head",
    "join",
    "leaderboard",
    "new_game",
    "norm",
    "other",
    "player_rows",
    "revealed_count",
    "settle",
    "tallies",
    "validate_new",
    "words_left",
    "words_of",
]
