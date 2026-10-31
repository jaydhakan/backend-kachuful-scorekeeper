"""What every upload derives (plan §7.3; app: src/domain/timeline.ts)."""

from dataclasses import dataclass

from app.domain.game import Game, Seat
from app.domain.names import name_key
from app.domain.rules import points, standings

AUTO_END_MS = 24 * 3600 * 1000


def pid_of(seat: Seat) -> str:
    """A seat's identity across games: the registry id (or the name, for pre-registry games)."""
    return seat.player_id if seat.player_id is not None else f"name:{name_key(seat.name)}"


@dataclass(frozen=True)
class RoundFact:
    """One player's part in one done round: a `round_scores` row."""

    game_id: str
    round_index: int
    player_id: str
    done_at: int
    cards: int
    max_cards: int
    trump: str
    bid: int
    outcome: str
    points: int
    is_dealer: bool
    hook_hits: int
    run_hot: int
    run_cold: int
    table_size: int
    made_count: int


@dataclass(frozen=True)
class GameFact:
    """One player's whole game: a `game_players` row."""

    game_id: str
    player_id: str
    local_id: str
    seat: int
    name: str
    emoji: str
    color: str
    points: int
    place: int | None
    won: bool
    rounds: int
    made: int
    missed: int
    best_round: int | None
    worst_round: int | None
    longest_hot: int
    longest_cold: int
    hook_hits: int
    ended_at: int | None


@dataclass(frozen=True)
class Ended:
    """When a game ended, and whether by the 24-hour auto-end."""

    at: int | None
    auto: bool


def done_at_of(game: Game, index: int) -> int:
    """A round's time; legacy rounds without one use the game's updatedAt."""
    done_at = game.rounds[index].done_at if index < len(game.rounds) else None
    return game.updated_at if done_at is None else done_at


def game_ended_at(game: Game, now_ms: int) -> Ended:
    """Finished → its finish time; idle for a day with a round done → its last round; else open."""
    if game.status == "finished":
        return Ended(
            at=game.finished_at if game.finished_at is not None else game.updated_at, auto=False
        )
    done = game.done_rounds
    if done and now_ms - game.updated_at >= AUTO_END_MS:
        last = max(game.updated_at if r.done_at is None else r.done_at for r in done)
        return Ended(at=last, auto=True)
    return Ended(at=None, auto=False)


def round_facts(game: Game) -> list[RoundFact]:
    """One row per player per done round, with the made/missed runs within the game."""
    out: list[RoundFact] = []
    hot: dict[str, int] = {}
    cold: dict[str, int] = {}
    for r in game.done_rounds:
        made_count = sum(1 for p in game.players if r.results.get(p.id) == "made")
        for seat_index, seat in enumerate(game.players):
            outcome = r.results.get(seat.id) or "missed"
            hot[seat.id] = hot.get(seat.id, 0) + 1 if outcome == "made" else 0
            cold[seat.id] = cold.get(seat.id, 0) + 1 if outcome == "missed" else 0
            is_dealer = seat_index == r.dealer_index
            out.append(
                RoundFact(
                    game_id=game.id,
                    round_index=r.index,
                    player_id=pid_of(seat),
                    done_at=game.updated_at if r.done_at is None else r.done_at,
                    cards=r.cards,
                    max_cards=game.max_cards,
                    trump=r.trump,
                    bid=int(r.bids.get(seat.id) or 0),
                    outcome=outcome,
                    points=points(r, seat.id) or 0,
                    is_dealer=is_dealer,
                    hook_hits=(r.hook_hits or 0) if is_dealer else 0,
                    run_hot=hot[seat.id],
                    run_cold=cold[seat.id],
                    table_size=len(game.players),
                    made_count=made_count,
                )
            )
    return out


def game_facts(
    game: Game, ended_at: int | None, rounds: list[RoundFact] | None = None
) -> list[GameFact]:
    """One row per seat: totals, place once ended, runs and hook hits."""
    facts = round_facts(game) if rounds is None else rounds
    by_seat = {s.seat.id: s for s in standings(game)}
    out: list[GameFact] = []
    for seat_index, seat in enumerate(game.players):
        pid = pid_of(seat)
        mine = [r for r in facts if r.player_id == pid]
        standing = by_seat[seat.id]
        place = standing.rank if ended_at is not None and mine else None
        scores = [r.points for r in mine]
        out.append(
            GameFact(
                game_id=game.id,
                player_id=pid,
                local_id=seat.id,
                seat=seat_index,
                name=seat.name,
                emoji=seat.emoji,
                color=seat.color,
                points=standing.total,
                place=place,
                won=place == 1,
                rounds=len(mine),
                made=sum(1 for r in mine if r.outcome == "made"),
                missed=sum(1 for r in mine if r.outcome == "missed"),
                best_round=max(scores) if scores else None,
                worst_round=min(scores) if scores else None,
                longest_hot=max([0, *(r.run_hot for r in mine)]),
                longest_cold=max([0, *(r.run_cold for r in mine)]),
                hook_hits=sum(r.hook_hits for r in mine),
                ended_at=ended_at,
            )
        )
    return out
