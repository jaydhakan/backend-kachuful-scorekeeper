"""Property tests: Spades scoring, legality and whole random games keep their invariants."""

import copy
import json
import random
from typing import Any

from hypothesis import event, given, settings
from hypothesis import strategies as st

from app.domain.spades import (
    STANDARD_RULES,
    ApplyCtx,
    ApplyResult,
    allowed_bids,
    apply_action,
    card_id,
    deck,
    legal_plays,
    new_table,
    play_problem,
    rank_value,
    score_hand,
    sit,
    table_hand,
    totals,
    trick_winner,
    tricks_ok,
    view_for,
)

T0 = 1_794_852_000_000
DECK = deck()


# --- scoring ---------------------------------------------------------------------------------


@st.composite
def hands(draw: st.DrawFn) -> dict[str, Any]:
    """A finished hand whose Nil tricks fit the bids and team a's tricks."""
    bids = [draw(st.integers(0, 13)) for _ in range(4)]
    team_a = draw(st.integers(0, 13))
    nil: list[int | None] = [None] * 4
    for first, team in ((0, team_a), (1, 13 - team_a)):
        seats = [s for s in (first, first + 2) if bids[s] == 0]
        if len(seats) == 2:
            nil[seats[0]] = draw(st.integers(0, team))
            nil[seats[1]] = team - (nil[seats[0]] or 0)
        elif seats:
            nil[seats[0]] = draw(st.integers(0, team))
    blind = [b == 0 and draw(st.booleans()) for b in bids]
    h = table_hand({"index": 0, "dealer": 3, "bids": bids, "teamA": team_a, "nilTricks": nil})
    return {**h, "blind": blind}


def _expected(h: dict[str, Any], team: int, before: int) -> dict[str, int]:
    seats = (team, team + 2)
    nils = [s for s in seats if h["bids"][s] == 0]
    nil_took = sum(h["nilTricks"][s] for s in nils)
    nil_points = sum(
        (200 if h["blind"][s] else 100) * (1 if h["nilTricks"][s] == 0 else -1) for s in nils
    )
    contract = sum(h["bids"][s] for s in seats if s not in nils)
    took = h["teamTricks"][team] - nil_took
    points, bags = nil_points + nil_took, nil_took
    if len(nils) < 2:
        made = took >= contract
        points += 10 * contract + (took - contract) if made else -10 * contract
        bags += took - contract if made else 0
    penalty = (before + bags) // 10 - before // 10
    return {"points": points, "bags": bags, "net": points - 100 * penalty}


@settings(max_examples=500, deadline=None)
@given(hands(), st.integers(0, 30), st.integers(0, 30))
def test_a_hand_scores_by_the_formula(h: dict[str, Any], bags_a: int, bags_b: int) -> None:
    assert tricks_ok(h, h["teamTricks"], h["nilTricks"])
    s = score_hand(h, {"a": bags_a, "b": bags_b})
    for team, (t, before) in enumerate((("a", bags_a), ("b", bags_b))):
        got = {k: s[t][k] for k in ("points", "bags", "net")}
        assert got == _expected(h, team, before)
        assert s[t]["net"] == s[t]["points"] - 100 * s[t]["penalty"]


@settings(max_examples=200, deadline=None)
@given(st.lists(hands(), min_size=1, max_size=12))
def test_totals_take_100_for_every_ten_bags_of_the_game(run: list[dict[str, Any]]) -> None:
    run = [{**h, "index": i} for i, h in enumerate(run)]
    t = totals(run)
    for team in ("a", "b"):
        lines = [score_hand(h)[team] for h in run]
        bags = sum(x["bags"] for x in lines)
        assert t[team]["bags"] == bags
        assert t[team]["score"] == sum(x["points"] for x in lines) - 100 * (bags // 10)


# --- legality --------------------------------------------------------------------------------


@st.composite
def positions(draw: st.DrawFn) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """A hand of 1 to 13 cards, a trick of 0 to 3 other cards, and whether spades are broken."""
    cards = draw(st.permutations(DECK))
    size, played = draw(st.integers(1, 13)), draw(st.integers(0, 3))
    leader = draw(st.integers(0, 3))
    trick = [{"seat": (leader + i) % 4, "card": c} for i, c in enumerate(cards[13 : 13 + played])]
    return list(cards[:size]), trick, draw(st.booleans())


@settings(max_examples=500, deadline=None)
@given(positions())
def test_a_legal_play_always_exists_and_problems_agree(
    p: tuple[list[dict[str, Any]], list[dict[str, Any]], bool],
) -> None:
    hand, trick, broken = p
    legal = legal_plays(hand, trick, broken)
    assert legal
    ids = {card_id(c) for c in legal}
    for c in hand:
        assert (play_problem(c, hand, trick, broken) is None) == (card_id(c) in ids)


@settings(max_examples=500, deadline=None)
@given(st.permutations(DECK), st.integers(0, 3))
def test_the_trick_goes_to_the_highest_spade_else_the_highest_of_the_led_suit(
    cards: list[dict[str, Any]], leader: int
) -> None:
    plays = [{"seat": (leader + i) % 4, "card": c} for i, c in enumerate(cards[:4])]
    winner = trick_winner(plays)
    assert winner in (0, 1, 2, 3)
    spades = [p for p in plays if p["card"]["suit"] == "spades"]
    led = plays[0]["card"]["suit"]
    pool = spades or [p for p in plays if p["card"]["suit"] == led]
    assert winner == max(pool, key=lambda p: rank_value(p["card"]))["seat"]


# --- whole games -----------------------------------------------------------------------------

_JUNK: list[Any] = [
    None,
    "play",
    {},
    {"type": None},
    {"type": ["play"]},
    {"type": "unknown"},
    {"type": "play"},
    {"type": "play", "card": {"rank": "1", "suit": "stars"}},
    {"type": "bid", "bid": 2.5},
    {"type": "bid", "bid": True},
    {"type": "bid", "bid": "3"},
    {"type": "replace", "seat": 9},
    {"type": "replace", "seat": 1},
    {"type": "rematch", "id": ""},
    {"type": "rematch", "id": 5},
    {"type": "react", "emoji": "x"},
    {"type": "react", "emoji": "\U0001f525"},
    {"type": "deal"},
    {"type": "drawForDeal"},
    {"type": "ready"},
    {"type": "unready"},
    {"type": "shufflePartners"},
    {"type": "look"},
    {"type": "blindNil"},
]
_ACTORS: list[Any] = [0, 1, 2, 3, None, True, 1.0, "1", 7]
_KINDS = ["legal"] * 24 + ["timeout", "timeout", "junk", "junk", "anyCard", "blindNil", "deal"]
_RULES = st.fixed_dictionaries(
    {
        "target": st.sampled_from([100, 200, None]),
        "timerSec": st.sampled_from([30, 60, None]),
        "nil": st.booleans(),
        "blindNil": st.booleans(),
        "minTeamBid": st.sampled_from([None, 4]),
        "mercy": st.sampled_from([None, -200]),
    }
).map(lambda r: {**r, "blindNil": r["blindNil"] and r["nil"]})


def _who(n: int) -> dict[str, Any]:
    return {"userId": f"u-{n}", "playerId": f"p-{n}", "name": f"P{n}", "emoji": "x", "color": "c"}


def _shuffled(rnd: random.Random) -> list[list[dict[str, Any]]]:
    cards = list(DECK)
    rnd.shuffle(cards)
    return [cards[i * 13 : i * 13 + 13] for i in range(4)]


class Game:
    """A table, its private hands, and its clock, driven one move at a time."""

    def __init__(self, rules: dict[str, Any], rnd: random.Random) -> None:
        s = new_table(id="t", code="KQ7M", name="Table", rules=rules, host=_who(0), now=T0)
        for n in (1, 2, 3):
            s = sit(s, _who(n), None, T0).state
        self.state, self.now, self.rnd = s, T0, rnd
        self.hands_t: list[list[dict[str, Any]]] | None = None
        for seat in range(4):
            self.apply(seat, {"type": "ready"})
        self.apply(0, {"type": "drawForDeal"}, draw=list(rnd.sample(DECK, 4)))
        self.apply(self.state["dealer"], {"type": "deal"}, deal=_shuffled(rnd))

    def apply(self, actor: Any, action: Any, dt: int = 1000, **ctx: Any) -> ApplyResult:
        """Applies one move and checks the invariants that hold after any move."""
        before, hands, now = copy.deepcopy(self.state), self.hands_t, self.now + dt
        r = apply_action(self.state, action, actor, ApplyCtx(now=now, hands=hands, **ctx))
        assert self.state == before, "the input state was mutated"
        if r.ok and not r.noop:
            assert r.state["rev"] == before["rev"] + 1
        else:
            assert r.state is self.state and r.hands is hands
        self.state, self.hands_t, self.now = r.state, r.hands, now
        _check(self.state, self.hands_t, now)
        return r


def _check(s: dict[str, Any], hands: list[list[dict[str, Any]]] | None, now: int) -> None:
    for h in s["history"]:
        assert sum(h["teamTricks"]) == 13
        assert len(h["tricks"]) == 13
        for t in h["tricks"]:
            assert len(t["plays"]) == 4 and t["winner"] == trick_winner(t["plays"])
    if s["status"] == "playing" and s["phase"] in ("bidding", "playing", "handEnd"):
        assert hands is not None
        seen = [c for h in hands for c in h] + [p["card"] for p in s["trick"]]
        seen += [p["card"] for t in s["tricks"] for p in t["plays"]]
        assert sorted(map(card_id, seen)) == sorted(map(card_id, DECK))
    for seat in (0, 1, 2, 3, None):
        view = view_for(s, hands, seat, now)
        mine = view["me"]["hand"]
        assert mine is None or (hands is not None and seat is not None and mine == hands[seat])
        public = {k: v for k, v in view["table"].items() if k not in ("drawn", "history")}
        text = json.dumps(public)
        for other in range(4):
            for c in hands[other] if hands is not None and other != seat else []:
                assert json.dumps(c) not in text


def _legal(g: Game, choice: int, dt: int) -> None:
    s = g.state
    seat = s["turn"]
    if s["phase"] in ("deal", "handEnd"):
        g.apply(seat, {"type": "deal"}, dt, deal=_shuffled(g.rnd))
    elif s["phase"] == "bidding":
        if not s["looked"][seat]:
            g.apply(seat, {"type": "look"}, dt)
        options = allowed_bids(seat, g.state["bids"], g.state["rules"])
        g.apply(seat, {"type": "bid", "bid": options[choice % len(options)]}, dt)
    elif g.hands_t is not None:
        legal = legal_plays(g.hands_t[seat], s["trick"], s["broken"])
        g.apply(seat, {"type": "play", "card": legal[choice % len(legal)]}, dt)


def _move(g: Game, kind: str, choice: int, dt: int) -> None:
    actor = _ACTORS[choice % len(_ACTORS)]
    if kind == "legal":
        _legal(g, choice, dt)
    elif kind == "timeout":
        rev = [None, g.state["rev"], g.state["rev"] - 1][choice % 3]
        seen = [None, g.now - 120_000, g.now][choice % 3]
        g.apply(actor, {"type": "timeout"}, dt, rev=rev, last_seen_at=seen)
    elif kind == "junk":
        g.apply(actor, _JUNK[choice % len(_JUNK)], dt)
    elif kind == "anyCard":
        g.apply(actor, {"type": "play", "card": DECK[choice % 52]}, dt)
    elif kind == "deal":
        g.apply(actor, {"type": "deal"}, dt, deal=_shuffled(g.rnd))
    else:
        g.apply(actor, {"type": kind}, dt)


@settings(max_examples=60, deadline=None)
@given(
    _RULES,
    st.randoms(use_true_random=False),
    st.lists(
        st.tuples(
            st.sampled_from(_KINDS),
            st.integers(0, 10_000),
            st.sampled_from([0, 10, 1000, 5000, 16_000, 31_000, 61_000]),
        ),
        min_size=200,
        max_size=800,
    ),
)
def test_random_games_keep_every_invariant(
    rules: dict[str, Any], rnd: random.Random, plan: list[tuple[str, int, int]]
) -> None:
    g = Game({**STANDARD_RULES, **rules}, rnd)
    for kind, choice, dt in plan:
        if g.state["status"] != "playing":
            break
        _move(g, kind, choice, dt)
    event(f"{g.state['status']} hands={len(g.state['history'])}")
