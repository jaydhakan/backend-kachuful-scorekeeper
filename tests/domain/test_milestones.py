"""Milestones: group marks across every kind and per game, firsts, and what a recompute writes."""

from app.domain.enums import ProgressionGame
from app.domain.milestones import (
    FirstFact,
    Milestone,
    MilestoneKind,
    RankedGame,
    first_milestones,
    group_milestones,
    milestone_diff,
)

SPADES = ProgressionGame.SPADES
KACHUFUL = ProgressionGame.KACHUFUL


def _ranked(game_id: str, n_all: int, n_game: int, game: ProgressionGame = SPADES) -> RankedGame:
    return RankedGame(game_id, game, 1000 * n_all, ("b", "a"), n_all, n_game)


def test_the_marks_make_group_milestones_for_every_kind_and_one_game() -> None:
    out = group_milestones([_ranked("g100", 100, 50), _ranked("g101", 101, 51)])
    assert [(m.key, m.game, m.game_id, m.n, m.players) for m in out] == [
        ("group:all:100", None, "g100", 100, ("a", "b")),
        ("group:spades:50", SPADES, "g100", 50, ("a", "b")),
    ]
    assert {m.played for m in out} == {SPADES}


def test_a_game_off_the_marks_makes_nothing() -> None:
    assert group_milestones([_ranked("g", 99, 49), _ranked("h", 5000 + 1, 1001)]) == []


def test_a_first_goes_to_the_earliest_game_ties_on_the_game_id() -> None:
    key = "first:spades:boston"
    facts = [
        FirstFact(key, SPADES, "g2", 2000, ("c",)),
        FirstFact(key, SPADES, "g1b", 1000, ("b",)),
        FirstFact(key, SPADES, "g1a", 1000, ("z", "a")),
    ]
    assert first_milestones(facts) == [
        Milestone(key, MilestoneKind.FIRST, SPADES, "g1a", ("a", "z"), 1000, SPADES)
    ]


def _m(key: str, game_id: str) -> Milestone:
    return Milestone(key, MilestoneKind.GROUP, None, game_id, ("a",), 1, KACHUFUL, 100)


def test_the_diff_writes_new_moved_and_changed_and_drops_the_gone() -> None:
    stored = {
        "group:all:100": _m("group:all:100", "g1"),
        "group:all:250": _m("group:all:250", "g2"),
        "group:all:500": _m("group:all:500", "g3"),
    }
    moved = _m("group:all:250", "g9")
    new = _m("group:all:1000", "g4")
    diff = milestone_diff(stored, [stored["group:all:100"], moved, new])
    assert diff.upsert == [new, moved]
    assert diff.created == [new, moved]
    assert diff.gone == ["group:all:500"]


def test_an_unchanged_set_writes_nothing() -> None:
    stored = {"group:all:100": _m("group:all:100", "g1")}
    diff = milestone_diff(stored, list(stored.values()))
    assert (diff.upsert, diff.created, diff.gone) == ([], [], [])
