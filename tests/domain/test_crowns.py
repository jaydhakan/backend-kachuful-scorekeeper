"""Crowns: medal holders from a season board, the Legend, and what changed."""

from app.domain.crowns import (
    Crown,
    CrownChange,
    CrownKind,
    crown_changes,
    legend_of,
    medal_holders,
    sitara_of,
)
from app.domain.leaderboard import Board, BoardRow


def _row(pid: str, rank: int | None, *, qualified: bool = True) -> BoardRow:
    return BoardRow(
        rank=rank,
        qualified=qualified,
        player_id=pid,
        value=0.0,
        points=0,
        games=0,
        rounds=0,
        wins=0,
        accuracy=0.0,
        longest_hot=0,
    )


def test_medals_are_the_qualified_podium_and_ties_share() -> None:
    board = Board([_row("b", 1), _row("a", 1), _row("c", 3), _row("d", None, qualified=False)], 50)
    assert medal_holders(board) == {
        CrownKind.MEDAL1: ("a", "b"),
        CrownKind.MEDAL2: (),
        CrownKind.MEDAL3: ("c",),
    }


def test_the_legend_needs_two_titles_and_ties_share() -> None:
    assert legend_of(["a"]) == ()
    assert legend_of(["a", "b", "a", "b", "c"]) == ("a", "b")
    assert legend_of([]) == ()


def test_only_changed_holders_are_changes() -> None:
    stored = {
        CrownKind.MEDAL1: Crown(CrownKind.MEDAL1, ("a",), 1, "g1"),
        CrownKind.SITARA: Crown(CrownKind.SITARA, ("b",)),
    }
    desired = {
        CrownKind.MEDAL1: Crown(CrownKind.MEDAL1, ("a",), 2, "g2"),
        CrownKind.SITARA: Crown(CrownKind.SITARA, ()),
        CrownKind.LEGEND: Crown(CrownKind.LEGEND, ("c",)),
    }
    assert crown_changes(stored, desired) == [
        CrownChange(CrownKind.SITARA, ("b",), (), 0),
        CrownChange(CrownKind.LEGEND, (), ("c",), 0),
    ]


def test_sitara_needs_two_games() -> None:
    assert sitara_of({"riya": 40}, 1) == ()


def test_sitara_ties_share_sorted() -> None:
    assert sitara_of({"riya": 40, "asha": 40, "dev": 10}, 2) == ("asha", "riya")


def test_sitara_goes_to_nobody_without_points_above_zero() -> None:
    assert sitara_of({"riya": 0, "asha": -10}, 3) == ()
    assert sitara_of({}, 3) == ()
