"""Dealing a key (app: deal.ts). The server's keys come from `secrets`; the app's seeded deal is for tests."""

import secrets

from app.domain.ishaara.types import BLANKS, TEAMS, Team, as_team, other

_RANDOM = secrets.SystemRandom()


def deal_key(starting: str | None = None) -> tuple[list[str], str]:
    """9 cards to the starting team, 8 to the other, 7 blanks and the trap, shuffled; random start if None."""
    first: Team = _RANDOM.choice(TEAMS) if starting is None else as_team(starting)
    key = [first] * 9 + [other(first)] * 8 + ["blank"] * BLANKS + ["trap"]
    _RANDOM.shuffle(key)
    return key, first
