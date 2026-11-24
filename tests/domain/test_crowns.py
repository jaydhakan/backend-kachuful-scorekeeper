"""Crowns: medal holders from a season board, the Legend, and what changed."""

from app.domain.crowns import (
    Crown,
    CrownChange,
    CrownKind,
    crown_changes,
    legend_of,
    medal_holders,
    podium_holders,
    sitara_of,
    win_sitara_of,
    win_tallies,
)
from app.domain.leaderboard import Board, BoardRow
from app.domain.progression import WinTally, win_rate_standings


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


def test_a_win_rate_podium_is_the_season_champions_board() -> None:
    tallies = [
        WinTally("a", 10, 7),
        WinTally("b", 10, 7),
        WinTally("c", 12, 6),
        WinTally("d", 4, 4),  # too few games to qualify
    ]
    assert podium_holders(win_rate_standings(tallies, 10)) == {
        CrownKind.MEDAL1: ("a", "b"),
        CrownKind.MEDAL2: (),
        CrownKind.MEDAL3: ("c",),
    }


def test_a_win_rate_sitara_has_most_wins_then_most_games() -> None:
    today = [WinTally("a", 3, 2), WinTally("b", 2, 2), WinTally("c", 5, 1)]
    assert win_sitara_of(today, 5) == ("a",)
    assert win_sitara_of([WinTally("a", 2, 1), WinTally("b", 2, 1)], 2) == ("a", "b")


def test_a_win_rate_sitara_needs_two_games_each_and_two_today() -> None:
    assert win_sitara_of([WinTally("a", 1, 1), WinTally("b", 2, 1)], 2) == ("b",)
    assert win_sitara_of([WinTally("a", 1, 1)], 1) == ()
    assert win_sitara_of([WinTally("a", 1, 1), WinTally("b", 1, 0)], 3) == ()


def test_win_tallies_count_games_and_wins() -> None:
    seats = [("g1", "a", True), ("g1", "b", False), ("g2", "a", False), ("g2", "b", True)]
    assert win_tallies(seats) == ([WinTally("a", 2, 1), WinTally("b", 2, 1)], 2)


def test_a_win_rate_sitara_needs_a_win() -> None:
    assert win_sitara_of([WinTally("a", 3, 0), WinTally("b", 2, 0)], 3) == ()
