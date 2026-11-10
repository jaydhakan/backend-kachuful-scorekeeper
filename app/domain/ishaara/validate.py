"""What's wrong with a new game's seats, words and rules (app: validate.ts)."""

from typing import Any

from app.domain.ishaara.clue import as_int, clue_format_ok, norm
from app.domain.ishaara.rules import MIN_TEAM
from app.domain.ishaara.text import utf16_len
from app.domain.ishaara.types import GRID, TEAMS

MAX_WORD_LEN = 16
MAX_PLAYERS = 20
_TIMERS = (60, 120, 180)


def board_word_ok(w: object) -> bool:
    """A word on the board: one or two words of letters (custom words may hold a space), at most 16."""
    if not isinstance(w, str):
        return False
    t = norm(w)
    if not t or len(t) > MAX_WORD_LEN:
        return False
    return all(clue_format_ok(p) for p in t.split(" "))


def _timer_ok(r: dict[str, Any]) -> bool:
    if "timerSec" not in r:
        return False
    return r["timerSec"] is None or as_int(r["timerSec"]) in _TIMERS


def _packs_ok(packs: object) -> bool:
    if not isinstance(packs, list) or len(packs) > 10:
        return False
    return all(isinstance(p, str) and utf16_len(p) <= 40 for p in packs)


def _rules_ok(r: object) -> bool:
    if not isinstance(r, dict) or not _timer_ok(r):
        return False
    if r.get("clueEntry") not in ("typed", "spoken"):
        return False
    if as_int(r.get("bestOf")) not in (1, 3):
        return False
    return _packs_ok(r.get("packs"))


def _seats_problem(players: list[Any]) -> str | None:
    if not all(isinstance(p, dict) for p in players):
        return "players"
    # The app's Set compares by identity for non-strings; seats come typed from the API, so ids must be strings.
    ids = [p.get("playerId") for p in players]
    if not all(isinstance(i, str) and i for i in ids) or len(set(ids)) != len(players):
        return "players"
    if any(p.get("role") not in ("clue-giver", "guesser") for p in players):
        return "players"
    if any(p.get("team") not in TEAMS for p in players):
        return "teams"
    teams = [[p for p in players if p["team"] == t] for t in TEAMS]
    if any(len(seats) < MIN_TEAM for seats in teams):
        return "teams"
    if any(sum(1 for p in seats if p["role"] == "clue-giver") != 1 for seats in teams):
        return "clue-givers"
    return None


def _words_problem(words: object) -> str | None:
    if not isinstance(words, list) or len(words) != GRID or not all(map(board_word_ok, words)):
        return "words"
    if len({norm(w) for w in words}) != GRID:
        return "duplicate-words"
    return None


def validate_new(players: list[Any], words: list[Any], rules: dict[str, Any]) -> str | None:
    """The first problem with a new game (players, teams, clue-givers, words, duplicate-words, rules)."""
    if not isinstance(players, list) or not MIN_TEAM * 2 <= len(players) <= MAX_PLAYERS:
        return "players"
    problem = _seats_problem(players) or _words_problem(words)
    if problem:
        return problem
    return None if _rules_ok(rules) else "rules"
