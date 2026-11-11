"""Ishaara boards (app: boards.ts): tallies, the leaderboard and head-to-head over finished games' rows.

Rows are the camelCase player rows plus `gameId` and `finishedAt`; the caller filters the period.
"""

from dataclasses import dataclass
from typing import Any

SORTS = ("wins", "rate", "found")
# Games before win % ranks a player.
MIN_GAMES = 3


@dataclass(slots=True)
class _Acc:
    player_id: str
    games: int = 0
    wins: int = 0
    words_found: int = 0
    wrong_picks: int = 0
    trap_hits: int = 0
    clue_games: int = 0
    clue_wins: int = 0
    clues_given: int = 0
    avg_sum: float = 0
    avg_games: int = 0
    penalties: int = 0


def _add_clue_giving(a: _Acc, r: dict[str, Any]) -> None:
    a.clue_games += 1
    if r["won"]:
        a.clue_wins += 1
    a.clues_given += r["cluesGiven"]
    if r["avgClueNumber"] is not None:
        a.avg_sum += r["avgClueNumber"]
        a.avg_games += 1
    a.penalties += r["penalties"]


def _add(a: _Acc, r: dict[str, Any]) -> None:
    a.games += 1
    if r["won"]:
        a.wins += 1
    a.words_found += r["wordsFound"]
    a.wrong_picks += r["wrongPicks"]
    if r["trapHit"]:
        a.trap_hits += 1
    if r["role"] == "clue-giver":
        _add_clue_giving(a, r)


def _tally_out(a: _Acc) -> dict[str, Any]:
    return {
        "playerId": a.player_id,
        "games": a.games,
        "wins": a.wins,
        "winRate": a.wins / a.games,
        "wordsFound": a.words_found,
        "wrongPicks": a.wrong_picks,
        "trapHits": a.trap_hits,
        "clueGames": a.clue_games,
        "clueWins": a.clue_wins,
        "cluesGiven": a.clues_given,
        "avgClueNumber": a.avg_sum / a.avg_games if a.avg_games > 0 else None,
        "penalties": a.penalties,
    }


def tallies(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Totals per player (camelCase, the app's IshaaraTally), in order of first appearance."""
    by: dict[str, _Acc] = {}
    for r in rows:
        pid = r["playerId"]
        _add(by.setdefault(pid, _Acc(player_id=pid)), r)
    return [_tally_out(a) for a in by.values()]


def _value(t: dict[str, Any], sort: str) -> float:
    field = {"wins": "wins", "rate": "winRate"}.get(sort, "wordsFound")
    value: float = t[field]
    return value


def _qualified(t: dict[str, Any], sort: str) -> bool:
    return sort != "rate" or t["games"] >= MIN_GAMES


def leaderboard(rows: list[dict[str, Any]], sort: str) -> list[dict[str, Any]]:
    """Ranked by the sort's value, then games, then playerId; equal values share a rank.

    Win % ranks only players with at least MIN_GAMES; the rest follow with a null rank.
    """
    ts = sorted(
        tallies(rows),
        key=lambda t: (not _qualified(t, sort), -_value(t, sort), -t["games"], t["playerId"]),
    )
    out: list[dict[str, Any]] = []
    rank, prev = 0, None
    for i, t in enumerate(ts):
        value = _value(t, sort)
        if not _qualified(t, sort):
            out.append({**t, "rank": None, "value": value})
            continue
        if prev is None or value != prev:
            rank = i + 1
        prev = value
        out.append({**t, "rank": rank, "value": value})
    return out


def _pairs(
    rows: list[dict[str, Any]], a: str, b: str
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    by_game: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows:
        if r["playerId"] in (a, b):
            by_game.setdefault(r["gameId"], {})["a" if r["playerId"] == a else "b"] = r
    return [(g["a"], g["b"]) for g in by_game.values() if "a" in g and "b" in g]


def head_to_head(rows: list[dict[str, Any]], a: str, b: str) -> dict[str, int]:
    """Two players across finished games both sat in: as teammates and as opponents."""
    out = {"games": 0, "together": 0, "togetherWins": 0, "against": 0, "aWins": 0, "bWins": 0}
    for ra, rb in _pairs(rows, a, b):
        out["games"] += 1
        if ra["team"] == rb["team"]:
            out["together"] += 1
            out["togetherWins"] += int(ra["won"])
        else:
            out["against"] += 1
            out["aWins"] += int(ra["won"])
            out["bWins"] += int(rb["won"])
    return out
