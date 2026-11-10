"""Ishaara's rules one by one, mirroring the app's rules, clue, validate, stats and boards tests."""

from dataclasses import dataclass, replace
from typing import Any

import pytest

from app.domain.ishaara import (
    SKIP_AFTER_MS,
    TABLE,
    UNDO_MS,
    Actor,
    apply_action,
    clue_allowed,
    clue_format_ok,
    clue_giver_of,
    deal_key,
    guesses_for,
    head_to_head,
    join,
    leaderboard,
    new_game,
    player_rows,
    revealed_count,
    tallies,
    validate_new,
    words_left,
)

T0 = 1_793_539_800_000  # 2026-11-01T13:30Z, the app kit's T0
WORDS = [
    "river",
    "kite",
    "train",
    "cricket",
    "mango",
    "bridge",
    "mirror",
    "ghost",
    "wedding",
    "rocket",
    "temple",
    "pepper",
    "lion",
    "ocean",
    "film",
    "market",
    "needle",
    "crown",
    "shadow",
    "clock",
    "garden",
    "dosa",
    "thunder",
    "pilot",
    "chai",
]
RULES: dict[str, Any] = {"timerSec": None, "clueEntry": "typed", "packs": ["everyday"], "bestOf": 1}


def _seat(n: str, team: str, role: str) -> dict[str, Any]:
    return {
        "id": f"s-{n}",
        "name": n.title(),
        "emoji": "🙂",
        "color": "#f5a524",
        "playerId": f"00000000-0000-4000-8000-0000000000{ord(n[0]):02d}",
        "team": team,
        "role": role,
    }


PLAYERS = [
    _seat("riya", "sun", "clue-giver"),
    _seat("arjun", "sun", "guesser"),
    _seat("dev", "moon", "clue-giver"),
    _seat("meera", "moon", "guesser"),
]


def giver(team: str) -> Actor:
    return Actor(kind="clue-giver", team="sun" if team == "sun" else "moon")


def plain_key(starting: str) -> list[str]:
    """Cards 0-8 the starting team's, 9-16 the other's, 17-23 blank, 24 the trap."""
    second = "moon" if starting == "sun" else "sun"
    return [starting] * 9 + [second] * 8 + ["blank"] * 7 + ["trap"]


@dataclass(frozen=True)
class Kit:
    state: dict[str, Any]
    key: list[str]
    now: int


def started(
    *, starting: str = "sun", rules: dict[str, Any] | None = None, key: list[str] | None = None
) -> Kit:
    """An active game (both clue-givers joined) with a known key."""
    state = new_game(
        game_id="50000000-0000-4000-8000-000000000001",
        players=PLAYERS,
        words=WORDS,
        rules={**RULES, **(rules or {})},
        starting=starting,
        now=T0,
    )
    state = join(join(state, "sun", T0 + 1000), "moon", T0 + 2000)
    return Kit(state=state, key=key or plain_key(starting), now=T0 + 3000)


def act(k: Kit, actor: Actor, action: dict[str, Any], dt: int = 1000) -> Kit:
    """Applies an action that must succeed, moving the clock on first."""
    now = k.now + dt
    r = apply_action(k.state, k.key, action, actor, now)
    assert r.ok, f"{action['type']} failed: {r.error}"
    return replace(k, state=r.state, now=now)


def fail(k: Kit, actor: Actor, action: dict[str, Any], dt: int = 1000) -> dict[str, str]:
    """Applies an action that must fail; returns the error."""
    r = apply_action(k.state, k.key, action, actor, k.now + dt)
    assert not r.ok and r.error is not None
    return r.error


def clue(k: Kit, word: str | None, number: int | str, dt: int = 1000) -> Kit:
    return act(k, giver(k.state["turn"]), {"type": "clue", "word": word, "number": number}, dt)


def pick(k: Kit, card: int, dt: int = 1000) -> Kit:
    return act(k, TABLE, {"type": "guess", "card": card}, dt)


def bad(reason: str) -> dict[str, str]:
    return {"code": "invalid_action", "reason": reason}


NOT_YOURS = {"code": "not_your_turn"}


# A new game


def test_waits_for_both_clue_givers_then_starts_with_the_starting_team() -> None:
    s = new_game(game_id="g", players=PLAYERS, words=WORDS, rules=RULES, starting="moon", now=T0)
    assert s["status"] == "waiting"
    assert not apply_action(s, [], {"type": "endTurn"}, TABLE, T0).ok
    s = join(s, "moon", T0 + 1)
    assert s["status"] == "waiting"
    s = join(s, "sun", T0 + 2)
    assert s["status"] == "active"
    assert s["turn"] == "moon"
    assert all(r is None for r in s["revealed"])


def test_joining_twice_changes_nothing() -> None:
    k = started()
    assert join(k.state, "sun", k.now) is k.state


def test_counts_9_words_for_the_starting_team_and_8_for_the_other() -> None:
    state = started(starting="moon").state
    assert words_left(state, "moon") == 9
    assert words_left(state, "sun") == 8


def test_clue_giver_of_finds_each_teams_seat() -> None:
    state = started().state
    assert clue_giver_of(state, "sun") == PLAYERS[0]
    assert clue_giver_of(state, "moon") == PLAYERS[2]


# Clues


def test_only_the_turn_teams_clue_giver_clues_once_per_turn() -> None:
    k = started()
    assert fail(k, giver("moon"), {"type": "clue", "word": "spice", "number": 2}) == NOT_YOURS
    assert fail(k, TABLE, {"type": "clue", "word": "spice", "number": 2}) == NOT_YOURS
    k2 = clue(k, "spice", 2)
    assert k2.state["clue"] == {"team": "sun", "word": "spice", "number": 2}
    assert fail(k2, giver("sun"), {"type": "clue", "word": "heat", "number": 1}) == bad(
        "clue-given"
    )


def test_clue_allows_number_plus_one_guesses_and_unlimited_for_0_and_inf() -> None:
    assert clue(started(), "spice", 2).state["guessesLeft"] == 3
    assert clue(started(), "spice", 0).state["guessesLeft"] == "inf"
    assert clue(started(), "spice", "inf").state["guessesLeft"] == "inf"


@pytest.mark.parametrize("number", [-1, 10, 1.5, True, "2", None])
def test_clue_rejects_numbers_outside_0_to_9_and_inf(number: object) -> None:
    action = {"type": "clue", "word": "x", "number": number}
    assert fail(started(), giver("sun"), action) == bad("clue-format")


def test_clue_without_a_number_is_a_format_error() -> None:
    assert fail(started(), giver("sun"), {"type": "clue", "word": "x"}) == bad("clue-format")


def test_typed_clue_is_blocked_when_it_echoes_a_face_down_word() -> None:
    k = started()
    action = {"type": "clue", "word": "Rivers", "number": 1}
    assert fail(k, giver("sun"), action) == bad("clue-blocked")
    action = {"type": "clue", "word": "two words", "number": 1}
    assert fail(k, giver("sun"), action) == bad("clue-format")


@pytest.mark.parametrize("word", [None, 3, ["spice"]])
def test_typed_clue_needs_a_string_word(word: object) -> None:
    action = {"type": "clue", "word": word, "number": 1}
    assert fail(started(), giver("sun"), action) == bad("clue-format")


def test_typed_clue_is_stored_composed_and_trimmed_with_its_case() -> None:
    k = clue(started(), "  Spice ", 1)
    assert k.state["clue"]["word"] == "Spice"
    assert k.state["log"][-1] == {
        "at": k.now,
        "team": "sun",
        "type": "clue",
        "number": 1,
        "word": "Spice",
    }


def test_spoken_mode_takes_only_the_number() -> None:
    k = started(rules={"clueEntry": "spoken"})
    action = {"type": "clue", "word": "spice", "number": 2}
    assert fail(k, giver("sun"), action) == bad("clue-format")
    k2 = clue(k, None, 2)
    assert k2.state["clue"] == {"team": "sun", "word": None, "number": 2}
    assert "word" not in k2.state["log"][-1]
    assert act(k, giver("sun"), {"type": "clue", "number": 1}).state["clue"]["word"] is None


# Guessing


def test_guessing_needs_a_clue_and_only_the_table_picks() -> None:
    k = started()
    assert fail(k, TABLE, {"type": "guess", "card": 0}) == bad("no-clue")
    k2 = clue(k, "spice", 2)
    assert fail(k2, giver("sun"), {"type": "guess", "card": 0}) == NOT_YOURS


def test_an_own_word_is_revealed_and_the_team_guesses_again() -> None:
    k = pick(clue(started(), "spice", 2), 0)
    assert k.state["revealed"][0] == "sun"
    assert k.state["turn"] == "sun"
    assert k.state["guessesLeft"] == 2
    assert k.state["guessedThisTurn"] == 1


def test_the_turn_ends_when_guesses_run_out() -> None:
    k = pick(pick(clue(started(), "spice", 1), 0), 1)
    assert k.state["turn"] == "moon"
    assert k.state["clue"] is None


def test_the_other_teams_word_is_revealed_for_them_and_ends_the_turn() -> None:
    k = pick(clue(started(), "spice", 2), 9)
    assert k.state["revealed"][9] == "moon"
    assert k.state["turn"] == "moon"
    assert words_left(k.state, "moon") == 7


def test_a_blank_ends_the_turn() -> None:
    k = pick(clue(started(), "spice", 2), 17)
    assert k.state["revealed"][17] == "blank"
    assert k.state["turn"] == "moon"


def test_the_trap_loses_the_game_at_once_for_the_guessing_team() -> None:
    k = pick(clue(started(), "spice", 2), 24)
    assert k.state["status"] == "finished"
    assert k.state["winner"] == "moon"
    assert k.state["endedBy"] == "trap"
    assert k.state["solution"] == k.key


@pytest.mark.parametrize("card", [25, -1, 1.5, "0", None, True])
def test_a_bad_card_is_rejected(card: object) -> None:
    k = clue(started(), "spice", 3)
    assert fail(k, TABLE, {"type": "guess", "card": card}) == bad("bad-card")


def test_a_revealed_card_cant_be_picked_again() -> None:
    k = pick(clue(started(), "spice", 3), 0)
    assert fail(k, TABLE, {"type": "guess", "card": 0}) == bad("revealed")


def test_a_team_with_every_word_found_wins_even_on_the_other_teams_turn() -> None:
    k = pick(clue(started(), "a", 1), 17)
    k = clue(k, "b", "inf")
    for card in range(9, 16):
        k = pick(k, card)
    k = act(k, TABLE, {"type": "endTurn"})
    k = pick(clue(k, "c", 1), 16)
    assert (k.state["status"], k.state["winner"], k.state["endedBy"]) == (
        "finished",
        "moon",
        "all-found",
    )


def test_the_guessing_team_wins_when_it_finds_its_last_word() -> None:
    k = clue(started(), "all", "inf")
    for card in range(9):
        k = pick(k, card)
    assert (k.state["winner"], k.state["endedBy"]) == ("sun", "all-found")


# Ending a turn


def test_end_turn_needs_at_least_one_guess() -> None:
    k = clue(started(), "spice", 2)
    assert fail(k, TABLE, {"type": "endTurn"}) == bad("no-guess")
    k2 = act(pick(k, 0), TABLE, {"type": "endTurn"})
    assert k2.state["turn"] == "moon"
    assert k2.state["log"][-1]["type"] == "end"
    assert k2.state["log"][-1]["team"] == "sun"


# The timer


def test_the_timer_starts_with_the_clue_and_ends_the_turn_only_after_the_deadline() -> None:
    k = clue(started(rules={"timerSec": 60}), "spice", 2)
    assert k.state["deadline"] == k.now + 60_000
    assert fail(k, TABLE, {"type": "timeout"}, 30_000) == bad("too-early")
    k2 = act(k, TABLE, {"type": "timeout"}, 60_000)
    assert k2.state["turn"] == "moon"
    assert k2.state["log"][-1]["type"] == "timeout"
    assert k2.state["log"][-1]["team"] == "sun"


def test_the_next_action_after_a_missed_deadline_ends_the_turn_first() -> None:
    k = clue(started(rules={"timerSec": 60}), "spice", 2)
    r = apply_action(k.state, k.key, {"type": "guess", "card": 0}, TABLE, k.now + 61_000)
    assert not r.ok
    assert r.state["turn"] == "moon"
    assert r.state["revealed"][0] is None


def test_the_timer_is_off_by_default() -> None:
    assert clue(started(), "spice", 2).state["deadline"] is None


# Not allowed and the penalty


def test_not_allowed_only_from_the_other_clue_giver_before_the_first_guess() -> None:
    k = clue(started(), "spice", 2)
    assert fail(k, giver("sun"), {"type": "notAllowed"}) == NOT_YOURS
    assert fail(k, TABLE, {"type": "notAllowed"}) == NOT_YOURS
    assert fail(pick(k, 0), giver("moon"), {"type": "notAllowed"}) == bad("too-late")
    assert fail(started(), giver("moon"), {"type": "notAllowed"}) == bad("no-clue")


def test_not_allowed_ends_the_turn_and_the_other_team_picks_its_own_word_free() -> None:
    k = act(clue(started(), "spice", 2), giver("moon"), {"type": "notAllowed"})
    assert k.state["turn"] == "moon"
    assert k.state["pending"] == {"penaltyFor": "sun", "since": k.now}
    assert fail(k, TABLE, {"type": "guess", "card": 9}) == bad("penalty-pending")
    action = {"type": "clue", "word": "x", "number": 1}
    assert fail(k, giver("moon"), action) == bad("penalty-pending")
    assert fail(k, giver("moon"), {"type": "penaltyReveal", "card": 0}) == bad("not-yours")
    assert fail(k, giver("sun"), {"type": "penaltyReveal", "card": 9}) == NOT_YOURS
    assert fail(k, giver("moon"), {"type": "penaltyReveal"}) == bad("bad-card")
    k = act(k, giver("moon"), {"type": "penaltyReveal", "card": 9})
    assert k.state["revealed"][9] == "moon"
    assert k.state["pending"] is None
    assert k.state["turn"] == "moon"


def test_penalty_reveal_without_a_penalty_is_rejected() -> None:
    assert fail(started(), giver("sun"), {"type": "penaltyReveal", "card": 0}) == bad("no-penalty")


def test_the_host_can_skip_the_pick_after_60_seconds() -> None:
    k = act(clue(started(), "spice", 2), giver("moon"), {"type": "notAllowed"})
    assert fail(k, TABLE, {"type": "skip"}, SKIP_AFTER_MS - 1) == bad("too-early")
    k2 = act(k, TABLE, {"type": "skip"}, SKIP_AFTER_MS)
    assert k2.state["pending"] is None
    assert all(r is None for r in k2.state["revealed"])
    assert fail(k2, TABLE, {"type": "skip"}) == bad("no-penalty")


def test_a_free_pick_of_the_last_word_wins() -> None:
    k = pick(clue(started(), "a", 1), 17)
    k = clue(k, "b", "inf")
    for card in range(9, 16):
        k = pick(k, card)
    k = act(k, TABLE, {"type": "endTurn"})
    k = act(clue(k, "c", 1), giver("moon"), {"type": "notAllowed"})
    k = act(k, giver("moon"), {"type": "penaltyReveal", "card": 16})
    assert (k.state["winner"], k.state["endedBy"]) == ("moon", "all-found")


# Undo


def test_undo_takes_back_the_last_reveal_from_the_table_within_10_seconds() -> None:
    k = pick(clue(started(), "spice", 2), 9)
    assert fail(k, giver("sun"), {"type": "undoLast"}) == NOT_YOURS
    k2 = act(k, TABLE, {"type": "undoLast"}, UNDO_MS)
    assert k2.state["revealed"][9] is None
    assert k2.state["turn"] == "sun"
    assert k2.state["guessesLeft"] == 3
    assert not any(e["type"] == "guess" for e in k2.state["log"])
    assert k2.state["log"][-1]["type"] == "undo"
    assert k2.state["log"][-1]["card"] == 9


def test_undo_not_after_10_seconds_nor_after_another_action() -> None:
    k = pick(clue(started(), "spice", 2), 0)
    assert fail(k, TABLE, {"type": "undoLast"}, UNDO_MS + 1) == bad("too-late")
    ended = act(k, TABLE, {"type": "endTurn"})
    assert fail(ended, TABLE, {"type": "undoLast"}) == bad("nothing-to-undo")


def test_a_rejected_action_leaves_the_undo_open() -> None:
    k = pick(clue(started(), "spice", 2), 0)
    fail(k, giver("moon"), {"type": "clue", "word": "x", "number": 1})
    assert act(k, TABLE, {"type": "undoLast"}).state["revealed"][0] is None


def test_undo_not_once_the_game_is_over() -> None:
    k = pick(clue(started(), "spice", 2), 24)
    assert fail(k, TABLE, {"type": "undoLast"}) == bad("finished")


# Resigning, finished games, malformed actions


def test_a_resign_ends_the_game_for_the_other_team() -> None:
    k = act(started(), TABLE, {"type": "resign", "team": "sun"})
    assert (k.state["status"], k.state["winner"], k.state["endedBy"]) == (
        "finished",
        "moon",
        "resign",
    )
    assert k.state["match"] == {"game": 1, "sunWins": 0, "moonWins": 1}


@pytest.mark.parametrize("team", ["stars", None, 1])
def test_a_resign_needs_a_team(team: object) -> None:
    assert fail(started(), TABLE, {"type": "resign", "team": team}) == bad("bad-team")


def test_nothing_applies_to_a_finished_game() -> None:
    k = act(started(), TABLE, {"type": "resign", "team": "sun"})
    assert fail(k, TABLE, {"type": "guess", "card": 0}) == bad("finished")


def test_an_unknown_action_type_is_rejected() -> None:
    assert fail(started(), TABLE, {"type": "dance"}) == bad("bad-action")
    assert fail(started(), TABLE, {}) == bad("bad-action")


def test_every_applied_action_bumps_rev_and_a_rejected_one_does_not() -> None:
    k = started()
    k2 = clue(k, "spice", 2)
    assert k2.state["rev"] == k.state["rev"] + 1
    r = apply_action(k2.state, k2.key, {"type": "endTurn"}, TABLE, k2.now)
    assert r.state["rev"] == k2.state["rev"]


def test_apply_action_never_mutates_its_input() -> None:
    k = clue(started(rules={"timerSec": 60}), "spice", 2)
    before = repr(k.state)
    apply_action(k.state, k.key, {"type": "guess", "card": 0}, TABLE, k.now + 1000)
    apply_action(k.state, k.key, {"type": "guess", "card": 0}, TABLE, k.now + 61_000)
    assert repr(k.state) == before


# Clue checks

BOARD_WORDS = ["SUN", "Train", "bake", "box", "ice cream"]


def board(words: list[str], revealed: tuple[int, ...] = ()) -> dict[str, Any]:
    return {
        "words": words,
        "revealed": ["blank" if i in revealed else None for i in range(len(words))],
    }


def test_clue_check_blocks_a_face_down_word_in_any_case() -> None:
    b = board(BOARD_WORDS)
    assert clue_allowed("sun", b) == {"ok": False, "reason": "same", "word": "SUN"}
    assert clue_allowed("TRAIN", b)["reason"] == "same"


@pytest.mark.parametrize(
    "word",
    ["trains", "trained", "training", "trainer", "boxes", "baked", "baking", "baker", "bakes"],
)
def test_clue_check_blocks_simple_forms(word: str) -> None:
    assert clue_allowed(word, board(BOARD_WORDS))["reason"] == "form"


def test_clue_check_blocks_a_clue_whose_board_word_is_a_form_of_it() -> None:
    assert clue_allowed("mountain", board(["mountains"]))["reason"] == "form"


def test_clue_check_blocks_a_face_down_word_as_a_whole_part() -> None:
    b = board(BOARD_WORDS)
    assert clue_allowed("sunlight", b) == {"ok": False, "reason": "part", "word": "SUN"}
    assert clue_allowed("moonsun", b)["reason"] == "part"
    assert clue_allowed("night-trains", b)["reason"] == "part"


def test_clue_check_does_not_block_a_short_word_buried_mid_clue() -> None:
    assert clue_allowed("unboxed", board(["box"])) == {"ok": True}


def test_a_word_becomes_usable_once_revealed() -> None:
    assert clue_allowed("sun", board(["sun"], (0,))) == {"ok": True}


@pytest.mark.parametrize("word", ["spice", "jugaad", "बारिश", "tea-time"])
def test_clue_check_allows_unrelated_words_devanagari_and_hyphens(word: str) -> None:
    assert clue_allowed(word, board(BOARD_WORDS)) == {"ok": True}


def test_clue_check_rejects_empty_clues() -> None:
    assert clue_allowed("  ", board(BOARD_WORDS)) == {"ok": False, "reason": "empty"}


@pytest.mark.parametrize("word", ["two words", "r2d2", "hi!", "-dash", "a--b"])
def test_clue_check_rejects_spaced_numeric_or_punctuated_clues(word: str) -> None:
    assert clue_allowed(word, board(BOARD_WORDS))["reason"] == "format"


def test_a_clue_is_capped_at_24_letters() -> None:
    assert clue_format_ok("a" * 24)
    assert not clue_format_ok("a" * 25)


def test_guesses_for_maps_numbers_to_guesses() -> None:
    assert [guesses_for(n) for n in (0, 1, 9, "inf")] == ["inf", 2, 10, "inf"]


# Validation


def test_validate_accepts_two_teams_of_two_with_one_clue_giver_each() -> None:
    assert validate_new(PLAYERS, WORDS, RULES) is None


def test_validate_needs_two_per_team_and_one_clue_giver_each() -> None:
    assert validate_new(PLAYERS[:3], WORDS, RULES) == "players"
    assert validate_new([{**p, "team": "sun"} for p in PLAYERS], WORDS, RULES) == "teams"
    guessers = [{**p, "role": "guesser"} for p in PLAYERS]
    assert validate_new(guessers, WORDS, RULES) == "clue-givers"


def test_validate_needs_distinct_player_ids_and_known_roles() -> None:
    assert validate_new([*PLAYERS[:3], PLAYERS[0]], WORDS, RULES) == "players"
    assert validate_new([*PLAYERS[:3], {**PLAYERS[3], "playerId": ""}], WORDS, RULES) == "players"
    assert validate_new([*PLAYERS[:3], {**PLAYERS[3], "role": "x"}], WORDS, RULES) == "players"


def test_validate_needs_25_distinct_words() -> None:
    assert validate_new(PLAYERS, WORDS[1:], RULES) == "words"
    assert validate_new(PLAYERS, [*WORDS[1:], "KITE"], RULES) == "duplicate-words"
    assert validate_new(PLAYERS, [*WORDS[1:], "ice cream"], RULES) is None
    assert validate_new(PLAYERS, [*WORDS[1:], "r2"], RULES) == "words"


@pytest.mark.parametrize(
    "change",
    [{"timerSec": 90}, {"bestOf": 2}, {"bestOf": True}, {"clueEntry": "sung"}, {"packs": "x"}],
)
def test_validate_checks_the_rules(change: dict[str, Any]) -> None:
    assert validate_new(PLAYERS, WORDS, {**RULES, **change}) == "rules"


def test_validate_needs_a_timer_field() -> None:
    rules = {k: v for k, v in RULES.items() if k != "timerSec"}
    assert validate_new(PLAYERS, WORDS, rules) == "rules"


# Stats


def _row_of(rows: list[dict[str, Any]], team: str, role: str) -> dict[str, Any]:
    return next(r for r in rows if r["team"] == team and r["role"] == role)


def test_player_rows_give_each_seat_its_teams_picks_and_the_giver_its_clues() -> None:
    k = pick(pick(clue(started(), "spice", 2), 0), 17)
    k = act(clue(k, "river", 3), giver("sun"), {"type": "notAllowed"})
    k = act(k, giver("sun"), {"type": "penaltyReveal", "card": 1})
    k = pick(pick(clue(k, "heat", "inf"), 2), 24)
    rows = player_rows(k.state)
    riya = _row_of(rows, "sun", "clue-giver")
    expected = {"won": False, "wordsFound": 2, "wrongPicks": 2, "trapHit": True, "cluesGiven": 2}
    assert riya | expected == riya
    assert (riya["avgClueNumber"], riya["penalties"]) == (2, 0)
    arjun = _row_of(rows, "sun", "guesser")
    assert (arjun["wordsFound"], arjun["wrongPicks"], arjun["cluesGiven"]) == (2, 2, 0)
    assert arjun["avgClueNumber"] is None
    dev = _row_of(rows, "moon", "clue-giver")
    assert (dev["won"], dev["cluesGiven"], dev["avgClueNumber"]) == (True, 1, 3)
    assert (dev["penalties"], dev["wordsFound"]) == (1, 0)
    assert revealed_count(k.state) == 5


def test_an_undone_guess_does_not_count() -> None:
    k = act(pick(clue(started(), "spice", 2), 9), TABLE, {"type": "undoLast"})
    assert player_rows(k.state)[0]["wrongPicks"] == 0


# Boards


def _board_row(game: str, player: str, team: str, won: bool, **o: Any) -> dict[str, Any]:
    row = {"gameId": game, "playerId": player, "team": team, "won": won, "finishedAt": 0}
    row |= {"role": "guesser", "wordsFound": 3, "wrongPicks": 1, "trapHit": False}
    row |= {"cluesGiven": 0, "avgClueNumber": None, "penalties": 0}
    return row | o


BOARD_ROWS = [
    _board_row("g1", "a", "sun", True, role="clue-giver", cluesGiven=4, avgClueNumber=2),
    _board_row("g1", "b", "sun", True),
    _board_row("g1", "c", "moon", False, trapHit=True),
    _board_row(
        "g2", "a", "moon", False, role="clue-giver", cluesGiven=3, avgClueNumber=3, penalties=1
    ),
    _board_row("g2", "b", "sun", True, wordsFound=8),
    _board_row("g2", "c", "sun", True),
    _board_row("g3", "a", "sun", True),
    _board_row("g3", "c", "moon", False),
]


def test_tallies_count_games_wins_picks_and_clue_giving() -> None:
    by = {t["playerId"]: t for t in tallies(BOARD_ROWS)}
    a = by["a"]
    assert (a["games"], a["wins"], a["clueGames"], a["clueWins"]) == (3, 2, 2, 1)
    assert (a["cluesGiven"], a["avgClueNumber"], a["penalties"]) == (7, 2.5, 1)
    assert by["c"]["trapHits"] == 1


def test_leaderboard_ranks_by_wins_sharing_ranks_on_ties() -> None:
    ranks = [(r["playerId"], r["rank"]) for r in leaderboard(BOARD_ROWS, "wins")]
    assert ranks == [("a", 1), ("b", 1), ("c", 3)]


def test_leaderboard_ranks_win_rate_only_from_3_games() -> None:
    ranks = [(r["playerId"], r["rank"]) for r in leaderboard(BOARD_ROWS, "rate")]
    assert ranks == [("a", 1), ("c", 2), ("b", None)]


def test_head_to_head_splits_teammates_from_opponents() -> None:
    assert head_to_head(BOARD_ROWS, "a", "b") == {
        "games": 2,
        "together": 1,
        "togetherWins": 1,
        "against": 1,
        "aWins": 0,
        "bWins": 1,
    }


# Dealing


@pytest.mark.parametrize("starting", ["sun", "moon", None])
def test_deal_key_always_deals_9_8_7_1_with_the_9_to_the_starting_team(
    starting: str | None,
) -> None:
    for _ in range(200):
        key, first = deal_key(starting)
        assert starting is None or first == starting
        assert len(key) == 25
        second = "moon" if first == "sun" else "sun"
        counts = {i: key.count(i) for i in (first, second, "blank", "trap")}
        assert counts == {first: 9, second: 8, "blank": 7, "trap": 1}


def test_deal_key_picks_either_team_to_start() -> None:
    assert {deal_key()[1] for _ in range(200)} == {"sun", "moon"}


def test_deal_key_rejects_an_unknown_team() -> None:
    with pytest.raises(ValueError, match="not a team"):
        deal_key("stars")
