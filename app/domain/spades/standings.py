"""Totals and the end of the game (app: standings.ts). Derived from the finished hands; never stored."""

from collections.abc import Sequence
from typing import Any, Literal

from app.domain.spades.scoring import BAG_LIMIT, BAG_PENALTY, scorable, score_hand
from app.domain.spades.seats import TEAMS

DRAW_RESULT: dict[str, str] = {"winner": "draw", "endedBy": "draw"}


def _scores_from(points: dict[str, Any], bags: dict[str, Any]) -> dict[str, Any]:
    return {t: points[t] - BAG_PENALTY * (bags[t] // BAG_LIMIT) for t in TEAMS}


def hand_lines(hands: Sequence[Any]) -> list[dict[str, Any]]:
    """Each finished hand scored in order, with the bags carried between them."""
    out: list[dict[str, Any]] = []
    bags: dict[str, Any] = {"a": 0, "b": 0}
    points: dict[str, Any] = {"a": 0, "b": 0}
    for h in hands:
        if not isinstance(h, dict) or h.get("phase") != "done" or not scorable(h):
            continue
        s = score_hand(h, bags)
        bags = {t: s[t]["bagsAfter"] for t in TEAMS}
        points = {t: points[t] + s[t]["points"] for t in TEAMS}
        out.append(
            {"index": h["index"], "a": s["a"], "b": s["b"], "after": _scores_from(points, bags)}
        )
    return out


def _team_total(lines: list[dict[str, Any]], t: str) -> dict[str, Any]:
    bags = sum(line[t]["bags"] for line in lines)
    penalties = bags // BAG_LIMIT
    points = sum(line[t]["points"] for line in lines)
    return {"score": points - BAG_PENALTY * penalties, "bags": bags, "penalties": penalties}


def totals(hands: Sequence[Any]) -> dict[str, dict[str, Any]]:
    """Each team's score (the sum of points less 100 per 10 bags), bags and penalties."""
    lines = hand_lines(hands)
    return {"a": _team_total(lines, "a"), "b": _team_total(lines, "b")}


def scores_of(t: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Just the two scores."""
    return {"a": t["a"]["score"], "b": t["b"]["score"]}


def game_over(s: dict[str, Any], r: dict[str, Any]) -> dict[str, str] | Literal["tie"] | None:
    """A team at the target or (mercy on) below it ends the game; equal scores play on ('tie')."""
    target, mercy = r.get("target"), r.get("mercy")
    reached = target is not None and (s["a"] >= target or s["b"] >= target)
    sunk = mercy is not None and (s["a"] < mercy or s["b"] < mercy)
    if not reached and not sunk:
        return None
    if s["a"] == s["b"]:
        return "tie"
    return {"winner": "a" if s["a"] > s["b"] else "b", "endedBy": "target" if reached else "mercy"}


def end_result(s: dict[str, Any]) -> dict[str, str]:
    """The host's End game: the higher total wins, equal totals draw."""
    winner = "draw" if s["a"] == s["b"] else "a" if s["a"] > s["b"] else "b"
    return {"winner": winner, "endedBy": "end"}
